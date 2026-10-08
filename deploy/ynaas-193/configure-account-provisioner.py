#!/usr/bin/env python
"""One-time, server-local setup of a restricted Keycloak service account.

Run as the owner of deploy/ynaas-193/.env on the 193 server.  This script reads
the existing temporary bootstrap admin secret without printing it, grants only
realm-management/manage-users to the service account, verifies its access, and
then writes its client secret to the private .env file.  It is safe to rerun.
"""

from __future__ import print_function

import binascii
import json
import os
import subprocess
import sys

try:
    from urllib import urlencode
except ImportError:
    from urllib.parse import urlencode


ROOT = os.path.dirname(os.path.abspath(__file__))
ENV_FILE = os.path.join(ROOT, '.env')
BASE_URL = 'https://127.0.0.1:19443/auth'
REALM = 'rice-research'
CLIENT_ID = 'ynaas-user-provisioner'


def read_env():
    with open(ENV_FILE, 'rb') as handle:
        content = handle.read().decode('utf-8')
    values = {}
    for line in content.splitlines():
        if '=' in line and not line.startswith('#'):
            key, value = line.split('=', 1)
            values[key] = value
    return content, values


def request(method, path, token=None, payload=None, form=False, expected=(200,)):
    command = [
        'curl', '-k', '-sS', '--max-time', '20', '-X', method,
        '-w', '\n%{http_code}', BASE_URL + path,
    ]
    if token:
        command += ['-H', 'Authorization: Bearer ' + token]
    if payload is not None:
        command += [
            '-H', 'Content-Type: application/x-www-form-urlencoded' if form else 'Content-Type: application/json',
            '--data-binary', '@-',
        ]
        body = (urlencode(payload) if form else json.dumps(payload)).encode('utf-8')
    else:
        body = None
    process = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    output, _ = process.communicate(body)
    if process.returncode:
        raise RuntimeError('Keycloak request failed: curl exit code {}'.format(process.returncode))
    raw, status_text = output.rsplit(b'\n', 1)
    status = int(status_text)
    if status not in expected:
        raise RuntimeError('Keycloak request failed: HTTP {} on {}'.format(status, path.split('?')[0]))
    return json.loads(raw.decode('utf-8')) if raw else None


def one_client(token, client_id):
    clients = request('GET', '/admin/realms/{}/clients?clientId={}'.format(REALM, client_id), token)
    matches = [item for item in clients if item.get('clientId') == client_id]
    if len(matches) != 1:
        raise RuntimeError('Expected one Keycloak client: ' + client_id)
    return matches[0]


def write_private_env(original, values):
    replacements = {
        'KEYCLOAK_PROVISION_CLIENT_ID': CLIENT_ID,
        'KEYCLOAK_PROVISION_CLIENT_SECRET': values['KEYCLOAK_PROVISION_CLIENT_SECRET'],
        'KEYCLOAK_RESEARCHER_ROLE_ID': values['KEYCLOAK_RESEARCHER_ROLE_ID'],
    }
    lines = original.splitlines()
    for key, value in replacements.items():
        existing = [i for i, line in enumerate(lines) if line.startswith(key + '=')]
        if len(existing) > 1:
            raise RuntimeError('Duplicate environment setting: ' + key)
        if existing:
            lines[existing[0]] = key + '=' + value
        else:
            lines.append(key + '=' + value)
    new_content = ('\n'.join(lines) + '\n').encode('utf-8')
    temporary = ENV_FILE + '.provisioning-tmp'
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, 'wb') as handle:
            handle.write(new_content)
            handle.flush()
            os.fsync(handle.fileno())
        os.rename(temporary, ENV_FILE)
    except Exception:
        if os.path.exists(temporary):
            os.unlink(temporary)
        raise


def main():
    original, values = read_env()
    username = values.get('KEYCLOAK_ADMIN_USERNAME')
    password = values.get('KEYCLOAK_ADMIN_PASSWORD')
    if not username or not password:
        raise RuntimeError('Temporary operations administrator is not configured')
    master_token = request('POST', '/realms/master/protocol/openid-connect/token', payload={
        'grant_type': 'password', 'client_id': 'admin-cli',
        'username': username, 'password': password,
    }, form=True)['access_token']

    existing = request('GET', '/admin/realms/{}/clients?clientId={}'.format(REALM, CLIENT_ID), master_token)
    matches = [item for item in existing if item.get('clientId') == CLIENT_ID]
    if not matches:
        generated = binascii.hexlify(os.urandom(32)).decode('ascii')
        request('POST', '/admin/realms/{}/clients'.format(REALM), master_token, payload={
            'clientId': CLIENT_ID,
            'name': 'Yunnan researcher account provisioning',
            'protocol': 'openid-connect',
            'enabled': True,
            'publicClient': False,
            'serviceAccountsEnabled': True,
            'standardFlowEnabled': False,
            'directAccessGrantsEnabled': False,
            'fullScopeAllowed': True,
            'secret': generated,
        }, expected=(201,))
    provisioner = one_client(master_token, CLIENT_ID)
    secret_response = request(
        'GET', '/admin/realms/{}/clients/{}/client-secret'.format(REALM, provisioner['id']), master_token
    )
    secret = secret_response.get('value')
    if not secret:
        raise RuntimeError('Keycloak did not return the service client secret')
    configured_secret = values.get('KEYCLOAK_PROVISION_CLIENT_SECRET', '')
    if configured_secret and configured_secret != secret:
        raise RuntimeError('Existing private service secret differs from Keycloak; refusing to overwrite')

    service_user = request(
        'GET', '/admin/realms/{}/clients/{}/service-account-user'.format(REALM, provisioner['id']), master_token
    )
    management = one_client(master_token, 'realm-management')
    manage_users = request(
        'GET', '/admin/realms/{}/clients/{}/roles/manage-users'.format(REALM, management['id']), master_token
    )
    researcher = request('GET', '/admin/realms/{}/roles/researcher'.format(REALM), master_token)
    request('POST', '/admin/realms/{}/users/{}/role-mappings/clients/{}'.format(
        REALM, service_user['id'], management['id']), master_token,
        payload=[{'id': manage_users['id'], 'name': 'manage-users'}], expected=(204,)
    )

    service_token = request('POST', '/realms/{}/protocol/openid-connect/token'.format(REALM), payload={
        'grant_type': 'client_credentials', 'client_id': CLIENT_ID, 'client_secret': secret,
    }, form=True)['access_token']
    request('GET', '/admin/realms/{}/users?max=1'.format(REALM), service_token)
    values['KEYCLOAK_PROVISION_CLIENT_SECRET'] = secret
    values['KEYCLOAK_RESEARCHER_ROLE_ID'] = researcher['id']
    write_private_env(original, values)
    print('Restricted account provisioning is configured and verified; no secrets were displayed.')


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('Configuration failed: {}'.format(exc), file=sys.stderr)
        sys.exit(1)
