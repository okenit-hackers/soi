from rest_framework.exceptions import ValidationError


class LemmingsError(Exception):
 pass


class DisabledActionError(ValidationError, LemmingsError):
 pass


class InvalidService(ValidationError, LemmingsError):
 pass


class SecurityError(LemmingsError, ValidationError):
 pass


class BotAccountProxyError(Exception):
 pass