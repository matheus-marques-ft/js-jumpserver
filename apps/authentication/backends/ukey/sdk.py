import os
import yaml
from django.conf import settings
from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from common.utils import get_logger
from common.const import Language
from .utils import detect_cert_algorithm


logger = get_logger(__name__)


class UKeySDKConfig:

    def __init__(self):
        if not settings.AUTH_UKEY:
            logger.debug('UKeySDKConfig: authentication backend not enabled')
            return

    def _vendor_path(self, filename):
        return os.path.join(
            settings.PROJECT_DIR,
            "apps", "authentication", "backends", "ukey", "vendors",
            settings.AUTH_UKEY_VENDOR, filename,
        )

    def get_sdk_script_path(self):
        return self._vendor_path('sdk_script.js')

    def get_sdk_config_path(self):
        return self._vendor_path('sdk_config.yaml')

    def load_sdk_script_content(self):
        """Return the SDK JS file content, cached per vendor; invalidated automatically when the vendor changes or the service restarts."""
        vendor = getattr(settings, 'AUTH_UKEY_VENDOR', '')
        cache_key = f'_sdk_script_cache'
        cache = getattr(self, cache_key, {})
        if vendor not in cache or settings.DEBUG_DEV:
            js_path = self.get_sdk_script_path()
            if not js_path or not os.path.isfile(js_path):
                return None
            with open(js_path, 'rb') as f:
                cache[vendor] = f.read()
            setattr(self, cache_key, cache)
        return cache[vendor]

    def load_sdk_config_content(self):
        """Return the raw YAML config data, cached per vendor; invalidated automatically when the vendor changes or the service restarts."""
        vendor = getattr(settings, 'AUTH_UKEY_VENDOR', '')
        cache_key = f'_sdk_config_cache'
        cache = getattr(self, cache_key, {})
        if vendor not in cache or settings.DEBUG_DEV:
            cf_path = self.get_sdk_config_path()
            if not cf_path or not os.path.isfile(cf_path):
                return {}
            cache[vendor] = self._load_yaml(cf_path)
            setattr(self, cache_key, cache)
        return cache[vendor]

    @staticmethod
    def _load_yaml(config_file):
        if not config_file or not os.path.isfile(config_file):
            logger.warning('UKeySDKConfig: config file not found: %s', config_file)
            return {}
        with open(config_file, 'r', encoding='utf-8') as f:
            return yaml.safe_load(f) or {}

    # ── CA / certificate chain (read-only system settings, not configurable in YAML) ──

    @property
    def ca_cert_content(self):
        """CA root certificate PEM content, read only from system settings."""
        return getattr(settings, 'AUTH_UKEY_CA_CERT_CONTENT', '') or ''

    @property
    def ca_key_content(self):
        """CA private key PEM content, read only from system settings."""
        return getattr(settings, 'AUTH_UKEY_CA_KEY_CONTENT', '') or ''

    @property
    def ca_key_pass(self):
        """CA private key password, read only from system settings."""
        return str(getattr(settings, 'AUTH_UKEY_CA_KEY_PASS', ''))
    
    @property
    def ca_cert_asym_alg(self):
        # Parse the signature algorithm type from the CA certificate content, returning a string such as 'RSA' or 'SM2' for use in the YAML config
        return detect_cert_algorithm(self.ca_cert_content)

    # ── Utilities ────────────────────────────────────────────────────────────

    @property
    def gmssl_bin(self):
        """gmssl binary path, defaults to 'gmssl' (looked up on the system PATH)."""
        return 'gmssl'

    # ── Authentication flow ─────────────────────────────────────────────────────

    @property
    def challenge_ttl(self):
        """Challenge code TTL in Redis (seconds), defaults to 300."""
        v = getattr(settings, 'AUTH_UKEY_CHALLENGE_TTL', 300)
        return int(v)

    # ── Certificate issuance ────────────────────────────────────────────────────

    @property
    def enroll_enabled(self):
        """Whether user certificate issuance is enabled."""
        v = getattr(settings, 'AUTH_UKEY_ENROLL_ENABLED', False)
        return bool(v)

    @property
    def enroll_validity_days(self):
        """Validity period of the issued certificate (days), defaults to 365."""
        v = getattr(settings, 'AUTH_UKEY_ENROLL_VALIDITY_DAYS', 365)
        return int(v)
    
    @property
    def default_pin(self):
        """Default certificate PIN code, defaults to an empty string (no PIN set)."""
        v = getattr(settings, 'AUTH_UKEY_DEFAULT_PIN', '')
        return str(v)

    # ── Vendor SDK mapping (raw data, serialized to the frontend by the API layer) ──
        
    @staticmethod
    def _render(sdk_config, trans_filter=None):
        """
        Only processes i18n translation markers in the YAML data; does not perform template variable substitution.
          - {{ 'text' | trans }} -> translated via trans_filter; if not provided, the original text is returned
        """
        import re
        _filter = trans_filter or (lambda s: s)
        _pattern = re.compile(r"""\{\{\s*(['"])(.+?)\1\s*\|\s*trans\s*\}\}""")

        def _translate(s):
            return _pattern.sub(lambda m: _filter(m.group(2)), s)

        def _walk(obj):
            if isinstance(obj, dict):
                return {k: _walk(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [_walk(item) for item in obj]
            if isinstance(obj, str):
                return _translate(obj)
            return obj

        return _walk(sdk_config)

    def _build_trans_filter(self, sdk_config, lang):
        """Build the Jinja2 | trans filter function, which looks up the translation for lang from the YAML i18n table.
        Returns the original text if no translation is found; language keys are automatically normalized (zh_hant -> zh-hant).
        """
        lang = Language.to_internal_code(lang)
        i18n_raw = sdk_config.get('i18n') or {}
        i18n = {
            text: {
                Language.to_internal_code(lk.replace('_', '-')): lv
                for lk, lv in entries.items()
            }
            for text, entries in i18n_raw.items()
            if isinstance(entries, dict)
        }

        def trans_filter(s):
            translations = i18n.get(str(s))
            if not translations:
                return s
            return translations.get(lang) or s

        return trans_filter


    def get_sdk_config(self, lang='en'):
        """Return the vendor SDK method mapping with the top-level 'cert'/'i18n' keys removed.
        Any string value in the YAML can be marked as translatable using the {{ 'text' | trans }} syntax.
        """
        sdk_config = self.load_sdk_config_content()
        trans_filter = self._build_trans_filter(sdk_config, lang)
        sdk_config = self._render(sdk_config, trans_filter)
        sdk_config = self._apply_internal_config_to_sdk_config(sdk_config)
        sdk_config = {k: v for k, v in sdk_config.items() if k not in ('i18n',)}
        return sdk_config
    
    # When a config value is a dict containing these keys, it is treated as an "algorithm branch dict" and automatically resolved based on the current certificate algorithm
    _ALGO_BRANCH_KEYS = frozenset({'SM2', 'RSA-1024', 'RSA-2048', 'default'})

    @classmethod
    def _is_algo_branch(cls, value):
        """Determine whether value is an algorithm branch dict (contains at least one known algorithm key)."""
        return isinstance(value, dict) and bool(cls._ALGO_BRANCH_KEYS & value.keys())

    def _resolve_algo_branch(self, branch, algo_key):
        """Get the value corresponding to the current algorithm from the algorithm branch dict; fall back to default if not found, and return None if that is also missing."""
        if algo_key in branch:
            return branch[algo_key]
        return branch.get('default')

    def _apply_internal_config_to_sdk_config(self, sdk_config):
        """Render the 'config' section and add it to data['config'] for use by the frontend API layer.

        Fields in the YAML config whose value is an algorithm branch dict (containing keys such as SM2/RSA-1024/RSA-2048/default)
        are automatically resolved to the corresponding scalar value based on the CA certificate's algorithm type, without needing to enumerate each field here.
        """
        config = sdk_config.get('config') or {}
        asym_alg_name = self.ca_cert_asym_alg

        # Automatically expand all algorithm branch dict fields
        resolved_config = {}
        for k, v in config.items():
            if self._is_algo_branch(v):
                resolved_config[k] = self._resolve_algo_branch(v, asym_alg_name)
            else:
                resolved_config[k] = v

        # Append backend-specific fields (not configured in the YAML config)
        resolved_config.update({
            'asym_alg_name': asym_alg_name,
            'challenge_ttl': self.challenge_ttl,
            'enroll': {
                'enabled': self.enroll_enabled,
                'validity_days': self.enroll_validity_days,
            },
            'pin': {
                'default': self.default_pin,
            },
            'api': {
                'ukey_sdk_script_url': reverse('api-auth:ukey:ukey-sdk-script'),
                'enroll_cert_url': reverse('api-auth:ukey:ukey-enroll-cert'),
                'user_detail_url': reverse('users:user-list') + '{user_id}/',
            },
            'api_body': {
                'enroll_cert_url': ['user_id', 'csr'],
                'user_detail_url': ['ukey_sn']
            },
            'api_method': {
                'ukey_sdk_script_url': ['GET'],
                'enroll_cert_url': ['POST'],
                'user_detail_url': ['PATCH'],
            }

        })
        sdk_config['config'] = resolved_config
        if not settings.DEBUG_DEV:
            sdk_config.pop('meta', None)
            sdk_config.pop('i18n', None)
        return sdk_config


ukey_sdk_config = UKeySDKConfig()
