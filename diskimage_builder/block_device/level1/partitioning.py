# Copyright 2016 Andreas Florath (andreas@florath.net)
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

import logging
import os

from diskimage_builder.block_device.exception import \
    BlockDeviceSetupException
from diskimage_builder.block_device.level1.mbr import MBR
from diskimage_builder.block_device.level1.partition import PartitionNode
from diskimage_builder.block_device.plugin import PluginBase
from diskimage_builder.block_device.utils import exec_sudo
from diskimage_builder.block_device.utils import parse_abs_size_spec
from diskimage_builder.block_device.utils import parse_rel_size_spec


logger = logging.getLogger(__name__)


class Partitioning(PluginBase):

    def __init__(self, config, default_config, state):
        logger.debug("Creating Partitioning object; config [%s]", config)
        super(Partitioning, self).__init__()

        # Unlike other PluginBase we are somewhat persistent, as the
        # partition nodes call back to us (see create() below).  We
        # need to keep this reference.
        self.state = state

        # Because using multiple partitions of one base is done
        # within one object, there is the need to store a flag if the
        # creation of the partitions was already done.
        self.number_of_partitions = 0

        # Parameter check
        if 'base' not in config:
            raise BlockDeviceSetupException("Partitioning config needs 'base'")
        self.base = config['base']

        if 'partitions' not in config:
            raise BlockDeviceSetupException(
                "Partitioning config needs 'partitions'")

        if 'label' not in config:
            raise BlockDeviceSetupException(
                "Partitioning config needs 'label'")
        self.label = config['label']
        if self.label not in ("mbr", "gpt"):
            raise BlockDeviceSetupException("Label must be 'mbr' or 'gpt'")

        # It is VERY important to get the alignment correct. If this
        # is not correct, the disk performance might be very poor.
        # Example: In some tests a 'off by one' leads to a write
        # performance of 30% compared to a correctly aligned
        # partition.
        # The problem for DIB is, that it cannot assume that the host
        # system uses the same IO sizes as the target system,
        # therefore here a fixed approach (as used in all modern
        # systems with large disks) is used.  The partitions are
        # aligned to 1MiB (which are about 2048 times 512 bytes
        # blocks)
        self.align = 1024 * 1024  # 1MiB as default
        if 'align' in config:
            self.align = parse_abs_size_spec(config['align'])

        self.partitions = []
        prev_partition = None

        for part_cfg in config['partitions']:
            np = PartitionNode(part_cfg, state, self, prev_partition)
            self.partitions.append(np)
            prev_partition = np

    def get_nodes(self):
        # return the list of partitions
        return self.partitions

    def _size_of_block_dev(self, dev):
        with open(dev, "r") as fd:
            fd.seek(0, 2)
            return fd.tell()

    def _create_mbr(self):
        """Create partitions with MBR"""
        with MBR(self.image_path, self.disk_size, self.align) as part_impl:
            for part_cfg in self.partitions:
                part_name = part_cfg.get_name()
                part_bootflag = PartitionNode.flag_boot \
                                in part_cfg.get_flags()
                part_primary = PartitionNode.flag_primary \
                               in part_cfg.get_flags()
                part_size = part_cfg.get_size()
                part_free = part_impl.free()
                part_type = part_cfg.get_type()
                logger.debug("Not partitioned space [%d]", part_free)
                part_size = parse_rel_size_spec(part_size,
                                                part_free)[1]
                part_no \
                    = part_impl.add_partition(part_primary, part_bootflag,
                                              part_size, part_type)
                logger.debug("Create partition [%s] [%d]",
                             part_name, part_no)

                # We're going to mount all partitions with kpartx
                # below once we're done.  So the device this partition
                # will be seen at becomes "/dev/mapper/loop0pX"
                assert self.device_path[:5] == "/dev/"
                partition_device_name = "/dev/mapper/%sp%d" % \
                                        (self.device_path[5:], part_no)
                self.state['blockdev'][part_name] \
                    = {'device': partition_device_name}

    def _create_gpt(self):
        """Create partitions with GPT"""

        cmd = ['sgdisk', self.image_path]

        # This padding gives us a little room for rounding so we don't
        # go over the end of the disk
        disk_free = self.disk_size - (2048 * 1024)
        pnum = 1

        for p in self.partitions:
            args = {}
            args['pnum'] = pnum
            args['name'] = '%s' % p.get_name()
            args['type'] = '%s' % p.get_type()

            # convert from a relative/string size to bytes
            size = parse_rel_size_spec(p.get_size(), disk_free)[1]

            # We keep track in bytes, but specify things to sgdisk in
            # megabytes so it can align on sensible boundaries. And
            # create partitions right after previous so no need to
            # calculate start/end - just size.
            assert size <= disk_free
            args['size'] = size // (1024 * 1024)

            new_cmd = ("-n", "{pnum}:0:+{size}M".format(**args),
                       "-t", "{pnum}:{type}".format(**args),
                       # Careful with this one, as {name} could have spaces
                       "-c", "{pnum}:{name}".format(**args))
            cmd.extend(new_cmd)

            # Fill the state; we mount all partitions with kpartx
            # below once we're done.  So the device this partition
            # will be seen at becomes "/dev/mapper/loop0pX"
            assert self.device_path[:5] == "/dev/"
            device_name = "/dev/mapper/%sp%d" % (self.device_path[5:], pnum)
            self.state['blockdev'][p.get_name()] \
                = {'device': device_name}

            disk_free = disk_free - size
            pnum = pnum + 1
            logger.debug("Partition %s added, %s remaining in disk",
                         pnum, disk_free)

        logger.debug("cmd: %s", ' '.join(cmd))
        exec_sudo(cmd)

    # not this is NOT a node and this is not called directly!  The
    # create() calls in the partition nodes this plugin has
    # created are calling back into this.
    def create(self):
        # This is a bit of a hack.  Each of the partitions is actually
        # in the graph, so for every partition we get a create() call
        # as the walk happens.  But we only need to create the
        # partition table once...
        self.number_of_partitions += 1
        if self.number_of_partitions > 1:
            logger.info("Not creating the partitions a second time.")
            return

        # the raw file on disk
        self.image_path = self.state['blockdev'][self.base]['image']
        # the /dev/loopX device of the parent
        self.device_path = self.state['blockdev'][self.base]['device']
        # underlying size
        self.disk_size = self._size_of_block_dev(self.image_path)

        logger.info("Creating partition on [%s] [%s]",
                    self.base, self.image_path)

        assert self.label in ('mbr', 'gpt')

        if self.label == 'mbr':
            self._create_mbr()
        elif self.label == 'gpt':
            self._create_gpt()

        # "saftey sync" to make sure the partitions are written
        exec_sudo(["sync"])

        # now all the partitions are created, get device-mapper to
        # mount them
        if not os.path.exists("/.dockerenv"):
            exec_sudo(["kpartx", "-avs", self.device_path])
        else:
            # If running inside Docker, make our nodes manually,
            # because udev will not be working. kpartx cannot run in
            # sync mode in docker.
            exec_sudo(["kpartx", "-av", self.device_path])
            exec_sudo(["dmsetup", "--noudevsync", "mknodes"])

        return

    def umount(self):
        # Remove the partition mappings made for the parent
        # block-device by create() above.  This is called from the
        # child PartitionNode umount.  Thus every
        # partition calls it, but we only want to do it once when
        # we know this is the very last partition
        self.number_of_partitions -= 1
        if self.number_of_partitions == 0:
            exec_sudo(["kpartx", "-d",
                       self.state['blockdev'][self.base]['device']])

    def cleanup(self):
        pass
