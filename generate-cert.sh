#!/bin/bash

openssl genrsa -des3 -passout pass:PASSWORD -out server.pass.key 2048
openssl rsa -passin pass:PASSWORD -in server.pass.key -out stylegan-web.key
rm server.pass.key
openssl req -new -key stylegan-web.key -out server.csr \
    -subj "/C=UK/ST=Warwickshire/L=Leamington/O=OrgName/OU=IT Department/CN=stylegan.com"
openssl x509 -req -days 365 -in server.csr -signkey stylegan-web.key -out stylegan-web.crt