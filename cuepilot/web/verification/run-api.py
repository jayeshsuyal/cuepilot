"""Run the unchanged backend with all test state confined to the frontend folder."""
import os
from pathlib import Path
import secrets
import sys

sys.dont_write_bytecode = True
WEB = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WEB.parent.parent))
from dotenv import load_dotenv
import uvicorn
from cuepilot.api import create_app

local_env = WEB / '.env.local'
if not local_env.exists():
    descriptor = os.open(local_env, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w') as handle:
        handle.write('CUEPILOT_OPERATOR_TOKEN=' + secrets.token_urlsafe(32) + '\n')
load_dotenv(local_env, override=False)
app = create_app(db_path=WEB / '.local-runtime' / 'cuepilot.sqlite3',
                 operator_token=os.environ['CUEPILOT_OPERATOR_TOKEN'],
                 bridge_token=secrets.token_urlsafe(32))
if __name__ == '__main__':
    uvicorn.run(app, host='127.0.0.1', port=8787, access_log=False)
