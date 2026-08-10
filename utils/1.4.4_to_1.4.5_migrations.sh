#!/bin/bash
# 

host=127.0.0.1
port=3306
username=root
db=jumpserver

echo "Backing up the original migrations"
mysqldump -u${username} -h${host} -P${port} -p ${db} django_migrations > django_migrations.sql.bak
ret=$?

if [ ${ret} == "0" ];then
    echo "Starting to use the new migrations file"
    mysql -u${username} -h${host} -P${port} -p ${db} < django_migrations.sql
else
    echo "Not valid"
fi


