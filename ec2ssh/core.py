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

import re, sys, fnmatch, logging, boto3
from cli import CliArgs

ami_users = {
    'amzn': 'ec2-user',
    'centos': 'root',
    'ubuntu': 'ubuntu',
    'coreos': 'core',
    'datastax': 'ubuntu',
    'Amazon_CentOS_6-5-x86-64_1.0rev15': 'root',
    'nagios': 'ubuntu'
}

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
        filters.append({'Name': 'tag:' + fs[0], 'Values': [fs[1]]})
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
            users[image_id] = image_id
        else:
            users[image_id] = config.default_user
    return users[image_id]


def create_ec2_instances(ec2, instances, names_count, config):
    used_names = {}
    user_cache = {}
    ec2instances = {}
    for instance in instances.values():
        name = instance[0]
        if names_count[name] > 1:
            if not used_names.get(name):
                used_names[name] = 1
            else:
                used_names[name] += 1
            name += '-' + str(used_names[name])
        try:
            ec2instances[name] = ECInstance(name, fetch_user(ec2, user_cache, instance[1].image_id, config), instance[1].instance_id,
                                        instance[1].image_id, instance[1].key_name, instance[1].private_ip_address,
                                        instance[1].public_ip_address, instance[2])
        except:
            print >> sys.stderr, 'Could not obtain instance for {0}!'.format(name)
    return ec2instances


def generate_name(instance, tags, tags_dict):
    tag_values = []
    if tags is None:
        tag_values = tags_dict.values()
    if tags is not None:
        for tag in tags.split(','):
            tag_values += [tags_dict[tag]] if tags_dict.get(tag) is not None else []
    name = ("-".join(tag_values) if tag_values else instance.id).replace(" ", "-")
    return name


def fetch_instances(ec2, tags, filters, config):
    instances_tuple = {}
    names_count = {}
    # create a dictionary of tuples instance_id -> (name, instance, tags_dict)
    for instance in ec2.instances.filter(Filters=filters):
        tags_dict = convert_tags_to_dict(instance)
        name = generate_name(instance, tags, tags_dict)
        instances_tuple[instance.instance_id] = (name, instance, tags_dict)
        names_count[name] = names_count[name] + 1 if names_count.get(name) else 1
    return create_ec2_instances(ec2, instances_tuple, names_count, config)


def find_proxy(instances, proxy_name, prefix):
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
        return prefix + proxy


def print_host_config(instance, use_private, key_folder, proxy, dynamic_port, prefix):
    print 'Host ' + prefix + instance.name
    if use_private:
        print '  HostName ' + instance.private_ip
        using_private = True
    else:
        if instance.public_ip:
            print '  HostName ' + instance.public_ip
            using_private = False
        else:
            print '  HostName ' + instance.private_ip
            using_private = True
    print '  User ' + instance.user
    if instance.key:
        print '  IdentityFile ' + key_folder + instance.key + '.pem'
    if proxy:
        if prefix + instance.name == proxy:
            if dynamic_port:
                print '  DynamicForward *:' + str(dynamic_port)
        elif using_private:
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
        logging.info("connecting to ec2 with profile '%s'", profile)
        session = boto3.Session(profile_name=profile)
    else:
        logging.info("connecting to ec2 with default profile")
        session = boto3.Session()
    return session.resource('ec2')


def print_config_file(config, section):
    options = config.options(section)
    for option in options:
        try:
            logging.info("%s",  option)
        except:
            logging.info("exception on %s!", option)


def main():
    args = CliArgs()
    instances = fetch_instances(connect(args.aws_profile), args.tags, build_filters(args.name_filter), args)
    proxy = find_proxy(instances, args.proxy, args.prefix)
    print_global_config(args, args.prefix)
    print_all_hosts_config(instances, args.private, args.key_folder, proxy, args.dynamic_forward, args.prefix)

