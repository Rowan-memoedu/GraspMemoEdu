"""Dedicated DeepSeek settings for GraspMemoEdu content import."""
import os
from urllib.parse import urlsplit


PREFIX = 'GRASPMEMOEDU_IMPORT_'


def settings():
    """Read live Windows user values; never borrow another workflow's key."""
    values = {name: os.environ.get(PREFIX + name, default) for name, default in (
        ('API_KEY', ''), ('BASE_URL', 'https://api.deepseek.com'), ('MODEL', 'deepseek-flash'))}
    if os.name == 'nt':
        import winreg
        try:
            with winreg.OpenKey(winreg.HKEY_CURRENT_USER, 'Environment') as key:
                for name in values:
                    try:
                        value, _ = winreg.QueryValueEx(key, PREFIX + name)
                        values[name] = value if isinstance(value, str) else ''
                    except FileNotFoundError:
                        pass
        except FileNotFoundError:
            pass
    values = {name: value.strip() for name, value in values.items()}
    if not values['API_KEY']:
        raise ValueError(PREFIX + 'API_KEY is not configured')
    parsed = urlsplit(values['BASE_URL'])
    if (parsed.scheme != 'https' or parsed.hostname != 'api.deepseek.com'
            or parsed.port not in (None, 443) or parsed.path not in ('', '/', '/v1', '/v1/')
            or parsed.username or parsed.password or parsed.query or parsed.fragment):
        raise ValueError('Import credential requires the official DeepSeek HTTPS endpoint')
    if not values['MODEL']:
        raise ValueError(PREFIX + 'MODEL is empty')
    return dict(api_key=values['API_KEY'], base_url=values['BASE_URL'], model=values['MODEL'])
