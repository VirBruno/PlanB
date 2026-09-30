"""Redacción de credenciales y enlaces de confirmación en logs de aplicación."""
import logging
import re

class SensitiveDataFilter(logging.Filter):
    def filter(self, record):
        message = record.getMessage()
        message = re.sub(r'(/auth/confirmar-email/)\?[^\s"]*', r'\1?[REDACTADO]', message)
        message = re.sub(r"""(?i)(authorization[\s'"]*[:=][\s'"]*)(?:bearer\s+)?[^\s,'"}]+""", r"\1[REDACTADO]", message)
        message = re.sub(r"""(?i)((?:token_hash|access_token|refresh_token|password|apikey)[\s'"]*[:=][\s'"]*)[^\s,'"}&]+""", r"\1[REDACTADO]", message)
        record.msg, record.args = message, ()
        return True
