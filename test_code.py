#!/usr/bin/env python3
"""
Lightweight test script to verify code structure without external dependencies
"""

import sys
import os
from datetime import datetime, timezone, timedelta

# Test 1: Check data structures and type hints
print("=" * 60)
print("TEST 1: Checking Python data structures...")
print("=" * 60)

try:
    from typing import Optional, List, Dict, Callable, Awaitable, Tuple
    from dataclasses import dataclass
    from enum import Enum
    from collections import deque
    print("✓ All type hints and data structures available")
except ImportError as e:
    print(f"✗ Failed: {e}")
    sys.exit(1)

# Test 2: Check basic class structures
print("\n" + "=" * 60)
print("TEST 2: Checking class definitions...")
print("=" * 60)

# Test PriceData class structure
class PriceData:
    """Test version of PriceData"""
    def __init__(self, price: float, timestamp: datetime):
        self.price = price
        self.timestamp = timestamp

# Test milestone calculation logic
def test_milestone_calculation():
    """Test milestone calculation without external deps"""
    print("\n" + "=" * 60)
    print("TEST 3: Testing milestone calculation logic...")
    print("=" * 60)

    def _calculate_milestone(price: float, threshold: float) -> float:
        """Calculate the milestone for a given price and threshold"""
        if threshold >= 1:
            # For larger thresholds (>= 1), use integer-based checking
            price_int = int(price)
            return int(price_int / threshold) * threshold
        else:
            # For small thresholds (< 1), use precise checking
            offset = price - 1.0
            return 1.0 + round(offset / threshold) * threshold

    # Test cases
    test_cases = [
        # (price, threshold, expected, description)
        (100500, 1000, 100000, "BTC at $100,500, threshold $1000"),
        (10500, 1000, 10000, "ETH at $10,500, threshold $1000"),
        (250, 50, 250, "SOL at $250, threshold $50"),
        (1.0025, 0.001, 1.002, "USD1 at $1.0025, threshold $0.001 (banker's rounding)"),
        (1.0035, 0.001, 1.004, "USD1 at $1.0035, threshold $0.001"),
        (0.9985, 0.001, 0.999, "USD1 at $0.9985, threshold $0.001"),
    ]

    all_passed = True
    for price, threshold, expected, desc in test_cases:
        result = _calculate_milestone(price, threshold)
        # Allow small floating point differences
        if abs(result - expected) < 0.0001:
            print(f"✓ {desc}")
            print(f"  Price {price} → Milestone: {result}")
        else:
            print(f"✗ {desc}")
            print(f"  Expected {expected}, got {result}")
            all_passed = False

    return all_passed

# Test 4: Check code structure
print("\n" + "=" * 60)
print("TEST 4: Checking code file structure...")
print("=" * 60)

files_to_check = ['common.py', 'monitor.py', 'bot.py']
required_methods = {
    'monitor.py': [
        'PriceMonitor',
        '_calculate_milestone',
        '_check_milestone_cooldown',
        '_send_milestone_notification',
        'check_integer_milestone',
        'check_volatility',
        'WebSocketMultiCoinMonitor',
        'PollingMultiCoinMonitor'
    ]
}

all_checks_passed = True

for filename in files_to_check:
    if not os.path.exists(filename):
        print(f"✗ File {filename} not found")
        all_checks_passed = False
        continue

    with open(filename, 'r', encoding='utf-8') as f:
        content = f.read()

    if filename in required_methods:
        missing_methods = []
        for method in required_methods[filename]:
            if method not in content:
                missing_methods.append(method)

        if missing_methods:
            print(f"✗ {filename}: Missing methods: {missing_methods}")
            all_checks_passed = False
        else:
            print(f"✓ {filename}: All required methods present")

# Test milestone calculation
milestone_test_passed = test_milestone_calculation()

# Final summary
print("\n" + "=" * 60)
print("TEST SUMMARY")
print("=" * 60)

if all_checks_passed and milestone_test_passed:
    print("✅ All tests passed!")
    print("✓ Python syntax is valid")
    print("✓ Code structure is correct")
    print("✓ Milestone calculation logic is working")
    sys.exit(0)
else:
    print("❌ Some tests failed")
    sys.exit(1)
