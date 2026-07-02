#!/usr/bin/env python
"""
Test runner for FlowGraph Backend.

Usage:
    python run_tests.py              # Run all tests
    python run_tests.py -v           # Verbose output
    python run_tests.py -k test_name # Run specific test
"""

import subprocess
import sys

def run_tests():
    """Run pytest with appropriate flags."""
    cmd = [
        sys.executable,
        "-m",
        "pytest",
        "Backend/tests",
        "-v",
        "--tb=short",
        "--color=yes",
    ] + sys.argv[1:]
    
    result = subprocess.run(cmd)
    return result.returncode

if __name__ == "__main__":
    exit_code = run_tests()
    sys.exit(exit_code)
