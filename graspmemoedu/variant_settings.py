"""Dedicated credentials for math bank Practice replenishment."""
import os

PREFIX = 'GRASPMEMOEDU_VARIANT_'


def settings():
    defaults = {'API_KEY': '', 'BASE_URL': 'https://api.deepseek.com',
                'MODEL': 'deepseek-flash', 'REASONING_EFFORT': 'low'}
    values = dict(defaults)
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
    values.update({name: os.environ[PREFIX + name] for name in values if os.environ.get(PREFIX + name)})
    values = {name: value.strip() for name, value in values.items()}
    if not values['API_KEY'] or values['API_KEY'].startswith('__ENTER'):
        raise ValueError(PREFIX + 'API_KEY is not configured')
    if values['MODEL'] != 'deepseek-flash' or values['REASONING_EFFORT'] not in ('low', 'high', 'max'):
        raise ValueError('Invalid variant model or reasoning effort')
    # CompatibleClient independently enforces the official HTTPS endpoint.
    return values
