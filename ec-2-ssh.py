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

import argparse
import re
import sys
import fnmatch
import ConfigParser
import logging
import boto3

ami_users = {
    'amzn': 'ec2-user',
    'centos': 'root',
    'ubuntu': 'ubuntu',
    'coreos': 'core',
    'datastax': 'ubuntu'
}

default_config_file = '/tmp/ec2sshconfig'
logging.basicConfig(stream=sys.stderr, level=logging.FATAL)


class ECInstance:
    def __init__(self, name, user, instance_id, image_id, key, private_ip, public_ip, tags):
        self.name = name
        self.user = user
        self.id = instance_id
        self.image_id = image_id
        self.tags = tags
        self.key = key
        self.private_ip = private_ip
        self.public_ip = public_ip

    def __str__(self):
        return "ECInstance [id:%s, name:%s, user:%s, image_id:%s]" % (self.id, self.name, self.user, self.image_id)


class HostConfig:
    def __init__(self, user, default_user, private, dynamic_forward, key_folder):
        self.user = user
        self.default_user = default_user
        self.private = private
        self.dynamic_forward = dynamic_forward
        self.key_folder = key_folder if key_folder.endswith("/") else key_folder + '/'


class GlobalConfig:
    def __init__(self, no_strict_check, no_host_key_check, keep_alive):
        self.no_strict_check = no_strict_check
        self.no_host_key_check = no_host_key_check
        self.keep_alive = keep_alive


class Arguments:
    def __init__(self, cmdArgs, configArgs):
        self.profile = cmdArgs.profile
        self.aws_profile = self.__get_argument(cmdArgs.profile, cmdArgs.aws_profile, configArgs, 'profile')
        self.tags = self.__get_argument(cmdArgs.profile, cmdArgs.tags, configArgs, 'tags')
        self.prefix = self.__get_argument(cmdArgs.profile, cmdArgs.prefix, configArgs, 'prefix')
        self.name_filter = self.__get_argument(cmdArgs.profile, cmdArgs.name_filter, configArgs, 'name_filter')
        self.proxy = self.__get_argument(cmdArgs.profile, cmdArgs.proxy, configArgs, 'proxy')
        self.private = self.__get_argument(cmdArgs.profile, cmdArgs.private, configArgs, 'private')
        self.dynamic_forward = self.__get_argument(cmdArgs.profile, cmdArgs.dynamic_forward, configArgs, 'dynamic_forward')
        self.key_folder = self.__get_argument(cmdArgs.profile, cmdArgs.key_folder, configArgs, 'key_folder')
        self.user = self.__get_argument(cmdArgs.profile, cmdArgs.user, configArgs, 'user')
        self.default_user = self.__get_argument(cmdArgs.profile, cmdArgs.default_user, configArgs, 'default_user')
        self.no_strict_check = self.__get_argument(cmdArgs.profile, cmdArgs.no_strict_check, configArgs, 'no_strict_check')
        self.no_host_key_check = self.__get_argument(cmdArgs.profile, cmdArgs.no_host_key_check, configArgs, 'no_host_key_check')
        self.keep_alive = self.__get_argument(cmdArgs.profile, cmdArgs.keep_alive, configArgs, 'keep_alive')

    def __get_argument(self, section, arg, configArgs, option):
        if arg:
            return arg
        elif configArgs.has_option(section, arg):
            return configArgs.get(section, option)


def parse_arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument('--profile', help='Specify ec-2-ssh profile to use')
    parser.add_argument('--aws-profile', help='Specify aws credential profile to use')
    parser.add_argument('--tags',
                        help='A comma-separated list of tag names to be considered for concatenation. If omitted, all tags will be used')
    parser.add_argument('--prefix', default='', help='Specify a prefix to prepend to all host names')
    parser.add_argument('--name-filter', action='append', help='<tag_name>=<value> to filter instances. Can be called multiple times')
    parser.add_argument('--proxy', help='name of the proxy server to use')
    parser.add_argument('--private', action='store_true', help='Use only private IP addresses')
    parser.add_argument('--dynamic-forward', type=int, help='Use dynamic forwarding when opening the proxy defined with --proxy')
    parser.add_argument('--key-folder', default='~/.ssh/', help='Location of the identity files folder')
    parser.add_argument('--user', help='Override the ssh username for all hosts')
    parser.add_argument('--default-user', help='Default ssh username to use if we cannot detect from AMI name')
    parser.add_argument('--no-strict-check', action='store_true', help='Disable strict host key checking')
    parser.add_argument('--no-host-key-check', action='store_true', help='Disable strict host key checking')
    parser.add_argument('--keep-alive', type=int, help='Disable strict host key checking')
    args = parser.parse_args()
    return args


def convert_tags_to_dict(ec2_object):
    tag_dict = {}
    if ec2_object.tags is None:
        return tag_dict
    for tag in ec2_object.tags:
        tag_dict[tag['Key']] = tag['Value']
    return tag_dict


def build_filters(filter_list):
    name_filters = (filter_list if filter_list is not None else [])
    filters = []
    for f in name_filters:
        fs = f.split("=", 1)
        filters.append(
            {
                'Name': 'tag:' + fs[0],
                'Values': [fs[1]]
            }
        )
    return filters + [{'Name': 'instance-state-name', 'Values': ['running']}]


def fetch_user(ec2, users, image_id, config):
    if config.user:
        return config.user
    if not users.get(image_id):
        image_name = ec2.Image(image_id).name
        for ami, user in ami_users.iteritems():
            if re.match(ami, image_name, re.I):
                users[image_id] = user
    if not users.get(image_id):
        if not config.default_user:
            print >> sys.stderr, 'Could not find a user for ami \'' + image_id + '\', please add to dictionary.'
        else:
            users[image_id] = config.default_user
    return users[image_id]


def generate_name(instance, tags, tags_dict):
    tag_values = []
    if tags is None:
        tag_values = tags_dict.values()
    if tags is not None:
        for tag in tags.split(','):
            tag_values += [tags_dict[tag]] if tags_dict.get(tag) is not None else []
    name = ("-".join(tag_values) if tag_values else instance.id).replace(" ", "-")
    return name


def create_ec2_instances(ec2, instances, names_count, config):
    used_names = {}
    user_cache = {}
    ec2instances = {}
    for instance in instances.values():
        print type(instance)
        name = instance[0]
        if names_count[name] > 1:
            if not used_names.get(name):
                used_names[name] = 1
            else:
                used_names[name] += 1
            name += '-' + str(used_names[name])
        ec2instances[name] = ECInstance(name, fetch_user(ec2, user_cache, instance[1].image_id, config), instance[1].instance_id,
                                        instance[1].image_id, instance[1].key_name, instance[1].private_ip_address,
                                        instance[1].public_ip_address, instance[2])
    return ec2instances


def fetch_instances(ec2, tags, filters, config):
    instances_tuple = {}
    user_cache = {}
    names_count = {}
    # create a dictionary of tuples instance_id -> (name, instance, tags_dict)
    for instance in ec2.instances.filter(Filters=filters):
        tags_dict = convert_tags_to_dict(instance)
        name = generate_name(instance, tags, tags_dict)
        instances_tuple[instance.instance_id] = (name, instance, tags_dict)
        names_count[name] = names_count[name] + 1 if names_count.get(name) else 1
    return create_ec2_instances(ec2, instances_tuple, names_count, config)


def find_proxy(instances, proxy_name):
    if proxy_name is not None:
        proxy = ''
        for inst in instances:
            filtered = fnmatch.fnmatch(inst, proxy_name)
            if filtered:
                if proxy:
                    print >> sys.stderr, 'More than one proxy name was discovered! '
                proxy = inst
        if not proxy:
            print >> sys.stderr, 'Could not find a proxy! '
        return proxy


def print_host_config(instance, use_private, key_folder, proxy, dynamic_port, prefix):
    print 'Host ' + prefix + instance.name
    if use_private:
        print '  HostName ' + instance.private_ip
    else:
        if instance.public_ip:
            print '  HostName ' + instance.public_ip
        else:
            print '  HostName ' + instance.private_ip
    print '  User ' + instance.user
    if instance.key:
        print '  IdentityFile ' + key_folder + instance.key + '.pem'
    if proxy:
        if instance.name == proxy:
            if dynamic_port:
                print '  DynamicForward ' + str(dynamic_port)
        else:
            print '  ProxyCommand ssh ' + proxy + ' /bin/nc %h %p 2> /dev/null'


def print_all_hosts_config(instances, use_private, key_folder, proxy, dynamic_port, prefix):
    for name in sorted(instances):
        print_host_config(instances[name], use_private, key_folder, proxy, dynamic_port, prefix)
        print


def print_global_config(global_config, prefix):
    if global_config.no_strict_check or global_config.no_host_key_check or global_config.keep_alive:
        print 'Host ' + prefix + '*'
        if global_config.no_strict_check:
            print '  StrictHostKeyChecking no'
        if global_config.no_host_key_check:
            print '  UserKnownHostsFile /dev/null'
        if global_config.keep_alive is not None:
            print '  ServerAliveInterval ' + str(global_config.keep_alive)
        print


def connect(profile):
    if profile:
        session = boto3.Session(profile_name=profile)
    else:
        session = boto3.Session()
    return session.resource('ec2')


def get_arguments():
    args = parse_arguments()
    parser = ConfigParser.SafeConfigParser()
    parser.read(default_config_file)
    return Arguments(args, parser)


def main():
    args = get_arguments()
    host_config = HostConfig(args.user, args.default_user, args.private, args.dynamic_forward, args.key_folder)
    global_config = GlobalConfig(args.no_strict_check, args.no_host_key_check, args.keep_alive)
    instances = fetch_instances(connect(args.profile), args.tags, build_filters(args.name_filter), host_config)
    proxy = find_proxy(instances, args.proxy)
    print_global_config(global_config, args.prefix)
    print_all_hosts_config(instances, args.private, args.key_folder, proxy, args.dynamic_forward, args.prefix)


if __name__ == '__main__':
    main()
