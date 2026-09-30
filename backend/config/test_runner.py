"""Impide conexiones de red accidentales en la suite habitual."""
from unittest.mock import patch
from django.test.runner import DiscoverRunner

class OfflineTestRunner(DiscoverRunner):
    def run_tests(self, test_labels, **kwargs):
        with patch("socket.socket.connect", side_effect=AssertionError("Red deshabilitada en tests")), \
             patch("socket.socket.connect_ex", side_effect=AssertionError("Red deshabilitada en tests")), \
             patch("socket.getaddrinfo", side_effect=AssertionError("DNS deshabilitado en tests")):
            return super().run_tests(test_labels, **kwargs)
