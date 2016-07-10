import argparse
import re
import sys
import fnmatch
import boto3

ami_users = {
    'amzn': 'ec2-user',
    'centos': 'root',
    'ubuntu': 'ubuntu',
    'coreos': 'core',
    'datastax': 'ubuntu'
}


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


def parse_arguments():
    parser = argparse.ArgumentParser()
    parser.add_argument('--profile', help='Specify aws credential profile to use')
    parser.add_argument('--tags', help='A comma-separated list of tag names to be considered for concatenation. If omitted, all tags will be used')
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
    parser.add_argument('--keep-alive', type=int,  help='Disable strict host key checking')

    args = parser.parse_args()
    return args


def convert_tags_to_dict(ec2_object):
    tag_dict = {}
    if ec2_object.tags is None:
        return tag_dict
    for tag in ec2_object.tags:
        tag_dict[tag['Key']] = tag['Value']
    return tag_dict


def generate_name(instance, tags, tags_dict):
    tag_values = []
    if tags is None:
        tag_values = tags_dict.values()
    if tags is not None:
        for tag in tags.split(','):
            tag_values += [tags_dict[tag]] if tags_dict.get(tag) is not None else []
    name = ("-".join(tag_values) if tag_values else instance.id).replace(" ", "-")
    return name


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


def fetch_instances(ec2, tags, filters, config):
    instances = {}
    user_cache = {}
    for inst in ec2.instances.filter(Filters=filters):
        tags_dict = convert_tags_to_dict(inst)
        name = generate_name(inst, tags, tags_dict)
        user = fetch_user(ec2, user_cache, inst.image_id, config)
        instances[name] = ECInstance(name, user, inst.instance_id, inst.image_id, inst.key_name, inst.private_ip_address,
                                     inst.public_ip_address, tags_dict)
    return instances


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
    for instance in instances.itervalues():
        print_host_config(instance, use_private, key_folder, proxy, dynamic_port, prefix)
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


def main():
    args = parse_arguments()
    host_config = HostConfig(args.user, args.default_user, args.private, args.dynamic_forward, args.key_folder)
    global_config = GlobalConfig(args.no_strict_check, args.no_host_key_check, args.keep_alive)
    instances = fetch_instances(connect(args.profile), args.tags, build_filters(args.name_filter), host_config)
    proxy = find_proxy(instances, args.proxy)
    print_global_config(global_config, args.prefix)
    print_all_hosts_config(instances, args.private, args.key_folder, proxy, args.dynamic_forward, args.prefix)


if __name__ == '__main__':
    main()
