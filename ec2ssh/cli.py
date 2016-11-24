#!/usr/bin/env python

"""
Licensed to the Apache Software Foundation (ASF) under one
or more contributor license agreements.  See the NOTICE file
distributed with this work for additional information
regarding copyright ownership.  The ASF licenses this file
to you under the Apache License, Version 2.0 (the
"License"); you may not use this file except in compliance
with the License.  You may obtain a copy of the License at

  http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing,
software distributed under the License is distributed on an
"AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
KIND, either express or implied.  See the License for the
specific language governing permissions and limitations
under the License.
"""

import argparse, logging, ConfigParser, os
from os.path import expanduser

class CliArgs:
    def __init__(self):
        args = self.__parse_arguments()
        self.profile = args.profile
        self.aws_profile = args.aws_profile
        self.tags = args.tags
        self.prefix = args.prefix
        self.name_filter = args.name_filter
        self.proxy = args.proxy
        self.private = args.private
        self.dynamic_forward = args.dynamic_forward
        self.key_folder = args.key_folder
        self.user = args.user
        self.default_user = args.default_user
        self.no_strict_check = args.no_strict_check
        self.no_host_key_check = args.no_host_key_check
        self.keep_alive = args.keep_alive

    # Credit: http://stackoverflow.com/a/5826167
    def __parse_arguments(self):
        home = expanduser("~")
        conf_parser = argparse.ArgumentParser(
            description=__doc__,
            formatter_class=argparse.RawDescriptionHelpFormatter,
            add_help=False
        )
        conf_parser.add_argument('--conf-file', default=home + '/.ec2ssh/credentials', metavar="FILE", help='Specify config file')
        conf_parser.add_argument('--profile', help='Specify ec-2-ssh profile to use')

        args, remaining_argv = conf_parser.parse_known_args()
        defaults = {'prefix': '', 'key_folder' : '~/.ssh/'}

        if args.conf_file:
            if not os.path.isfile(args.conf_file) :
                logging.info("conf file %s does not exist", args.conf_file)
            else:
                logging.info("using config file %s", args.conf_file)
                config = ConfigParser.SafeConfigParser()
                config.read([args.conf_file])
                file_args = {}
                if config.has_section(args.profile):
                    for key, value in  dict(config.items(args.profile)).iteritems():
                        file_args[key.replace('-', '_')] =  value
                    defaults.update(file_args)
                    logging.info('parsed options from conf file: %s', defaults)
                else:
                    logging.info("could not find profile '%s' in configuration file '%s'", args.profile, args.conf_file)

        parser = argparse.ArgumentParser(parents=[conf_parser])
        parser.set_defaults(**defaults)
        parser.add_argument('--aws-profile', help='Specify aws credential profile to use')
        parser.add_argument('--tags',help='A comma-separated list of tag names to be considered for concatenation. If omitted, all tags will be used')
        parser.add_argument('--prefix', help='Specify a prefix to prepend to all host names')
        parser.add_argument('--name-filter', action='append', help='<tag_name>=<value> to filter instances. Can be called multiple times')
        parser.add_argument('--proxy', help='name of the proxy server to use')
        parser.add_argument('--private', action='store_true', help='Use only private IP addresses')
        parser.add_argument('--dynamic-forward', type=int, help='Use dynamic forwarding when opening the proxy defined with --proxy')
        parser.add_argument('--key-folder', help='Location of the identity files folder')
        parser.add_argument('--user', help='Override the ssh username for all hosts')
        parser.add_argument('--default-user', help='Default ssh username to use if we cannot detect from AMI name')
        parser.add_argument('--no-strict-check', action='store_true', help='Disable strict host key checking')
        parser.add_argument('--no-host-key-check', action='store_true', help='Disable strict host key checking')
        parser.add_argument('--keep-alive', type=int, help='Disable strict host key checking')

        args = parser.parse_args(remaining_argv)
        logging.info("all args parsed: %s", args)
        if args.key_folder and not args.key_folder.endswith("/"):
            args.key_folder += "/"
        return args
