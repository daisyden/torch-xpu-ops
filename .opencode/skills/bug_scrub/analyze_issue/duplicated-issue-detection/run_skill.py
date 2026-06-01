#!/usr/bin/env python3
"""
Wrapper script to run the Duplicated Issue Detection skill.

This script invokes the duplicated_issue_detector.py module with proper path setup.

Usage:
    python3 run_skill.py [--excel EXCEL_FILE]

Example:
    python3 run_skill.py
    python3 run_skill.py --excel /path/to/torch_xpu_ops_issues.xlsx
"""

import os
import sys
import argparse
from pathlib import Path

SKILL_DIR = os.path.dirname(os.path.abspath(__file__))
_COMMON_DIR = str(Path(SKILL_DIR).resolve().parents[1] / "_common")
if _COMMON_DIR not in sys.path:
    sys.path.insert(0, _COMMON_DIR)
from paths import RESULT_DIR as _RESULT_DIR  # type: ignore[reportMissingImports] # noqa: E402

RESULT_DIR = os.environ.get('RESULT_DIR', str(_RESULT_DIR))

def main():
    parser = argparse.ArgumentParser(
        description='Run Duplicated Issue Detection Skill',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        '--excel', '-e', type=str, default=None,
        help=f'Path to Excel file (default: {RESULT_DIR}/torch_xpu_ops_issues.xlsx)'
    )
    
    args = parser.parse_args()
    
    excel_file = args.excel or os.path.join(RESULT_DIR, 'torch_xpu_ops_issues.xlsx')
    
    if not os.path.exists(excel_file):
        print(f"ERROR: Excel file not found: {excel_file}")
        return 1
    
    # Add skill directory and project root to path
    sys.path.insert(0, SKILL_DIR)
    sys.path.insert(0, os.path.join(SKILL_DIR, '..', '..', '..', 'issue_analysis', 'duplicated'))
    
    # Import and run the detector
    try:
        from duplicated_issue_detector import add_duplicated_column
        
        print(f"Loading: {excel_file}")
        print("Running Duplicated Issue Detection...")
        print("-" * 50)
        
        add_duplicated_column(excel_file)
        
        print("-" * 50)
        print("Complete!")
        return 0
        
    except ImportError as e:
        print(f"ERROR: Could not import detector: {e}")
        print(f"Make sure SKILL_DIR is correct: {SKILL_DIR}")
        return 1
    except Exception as e:
        print(f"ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1

if __name__ == '__main__':
    sys.exit(main())