==============
centos8
==============
Create a CentOS 8 image based on CentOS minmal.

See centos-minimal for further details. This element sets up additional packages and configurations needed in the SWITCHengines environment.

Note:
CentOS 8 comes with a new WebGUI called 'Cockpit Web Console'. This allows the user to configure the server, for example Service Management, User Account Management and more.
It is not enabled by default. 
Enable it via: 'sudo systemctl enable --now cockpit.socket'
To disable the notification, we delete the message while building the image with: 'rm /etc/motd.d/cockpit'. See 43-disable-cockpit-webconsole-notification

Get more information on: https://www.redhat.com/en/blog/managing-virtual-machines-rhel-8-web-console (RHEL 8 is a direct relative of CentOS 8)
