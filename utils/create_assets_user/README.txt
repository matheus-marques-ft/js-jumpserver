1. Install dependency packages
$ pip install requests

2. Set the account credentials and address
$ vim bulk_create_user.py # set these to the correct values
admin_username = 'admin'
admin_password = 'admin'
domain_url = 'http://localhost:8081'

3. Configure the system users to be added
$ vim system_users.txt
# name username password
test123 testq12 test123123123

3. Configure the system users to be added
$ vim system_users.txt
# name username password protocol[ssh,rdp] auto-push[0 don't push, 1 auto push]
test123 test123 test123123123 ssh 0

4. Run
$ python bulk_create_user.py
