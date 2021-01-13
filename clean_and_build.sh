#!/bin/bash

sudo rm -fr build diskimage_builder.egg-info
sudo rm -fr /usr/local/lib/python2.7/dist-packages/diskimage_builder*

sudo PBR_VERSION=2.18.666 python setup.py install -f -v

