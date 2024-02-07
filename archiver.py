import os
import shutil
import subprocess

import httpx
from dotenv import load_dotenv


load_dotenv()
BASE_URL = 'https://bioenergetic.forum/'
HEADERS = {'Content-Type': 'application/json'}
CREDENTIALS = {
  'username': os.environ['FORUM_USERNAME'],
  'password': os.environ['FORUM_PASSWORD'],
}
UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:53.0) Gecko/20100101 Firefox/53.0'
URLS_FILE = 'urls.txt'
WAIT_TIME = 10


def get_tids() -> None:
    with httpx.Client() as client:
        # create session
        response = client.post(
            BASE_URL+'api/v3/utilities/login', 
            headers=HEADERS,
            json=CREDENTIALS
        )
        response.raise_for_status()
        # get topic IDs
        response = client.get('https://bioenergetic.forum/api/recent')
        response.raise_for_status()
        tids = response.json()['tids']
        with open(URLS_FILE, 'w') as f:
            f.write('\n'.join([f'{BASE_URL}topic/{tid}' for tid in tids]))


def archive() -> None:
    cmd = [
        'wget',
        '--recursive',
        '--page-requisites',
        '--content-disposition',
        '--adjust-extension',
        '--convert-links',
        '--restrict-file-names=unix',
        '--no-parent',
        '--domains=bioenergetic.forum',
        f'-U "{UA}"',
        f'--wait={WAIT_TIME}',
        f'--input-file={URLS_FILE}',
    ]
    subprocess.run(cmd)


if __name__ == '__main__':
    get_tids()
    archive()
    shutil.make_archive('bioenergetic.forum', 'zip', 'bioenergetic.forum')

