# ec-2-ssh
Generate SSH config file from AWS EC2 . 

```
python ec2ssh --tags "Name" --aws-profile "profile" --name-filter "Name=*something*" --proxy "*proxy_server*"  --dynamic-forward 8051  --conf-file "my/conf/file"
```

