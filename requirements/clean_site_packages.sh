#!/bin/bash

python_version=$(python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
lib_path="/opt/py3/lib/python${python_version}/site-packages"

if [ ! -d "$lib_path" ]; then
  echo "lib_path $lib_path not exist"
  exit 0
fi

# Clean up unneeded modules
need_clean="jedi"
for i in $need_clean; do
  rm -rf "${lib_path}/${i}"
done

# Clean up unneeded modules in ansible connection
ansible_connection="${lib_path}/ansible_collections"
need_clean="fortinet dellemc f5networks netapp theforeman google azure cyberark ibm
            netbox purestorage inspur netapp_eseries sensu check_point vyos arista"
for i in $need_clean; do
  echo "rm -rf ${ansible_connection:-tmp}/${i}"
  rm -rf "${ansible_connection:-tmp}/${i}"
done

# Clean up cache files
cd ${lib_path} || exit 1
find . -name "*.pyc" -exec rm -f {} \;

# Clean up unneeded localization files
find . -name 'locale' -o -name 'locales' -type d | while read -r dir; do
    find "$dir" -mindepth 1 -maxdepth 1 -type d \
      ! -name 'zh_Hans' \
      ! -name 'zh_Hant' \
      ! -name 'zh_CN' \
      ! -name 'en' \
      ! -name 'en_US' \
      ! -name 'ja' \
      ! -name 'fr' \
      -exec rm -rf {} \;
done
