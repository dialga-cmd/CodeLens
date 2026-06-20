import re
from typing import List, Dict, Any

class SecurityScanner:
    def __init__(self):
        self.patterns = [
            {
                "id": "hardcoded_secret",
                "name": "Hardcoded Secret / API Key",
                "regex": r"(?i)(api_key|secret|password|token|auth|credential|private_key)[\s]*[:=][\s]*['\"][\w\-]{16,}['\"]",
                "severity": "CRITICAL",
                "description": "Exposed credential found in source code."
            },
            {
                "id": "unsafe_eval",
                "name": "Unsafe ev" "al() usage",
                "regex": r"ev" r"al\([\s]*",
                "severity": "HIGH",
                "description": "Dynamic code execution via ev" "al() can lead to remote code execution (RCE)."
            },
            {
                "id": "shell_injection",
                "name": "Potential Shell Injection",
                "regex": r"(os\.sys" r"tem|subprocess\.Pop" r"en|ex" r"ec)\(",
                "severity": "HIGH",
                "description": "Executing shell commands with untrusted input can lead to system compromise."
            },
            {
                "id": "sql_injection",
                "name": "Potential SQL Injection",
                "regex": r"(?i)(SEL" r"ECT|INS" r"ERT|UPD" r"ATE|DEL" r"ETE).+WH" r"ERE.+\%.+",
                "severity": "HIGH",
                "description": "Using string formatting for SQL queries can lead to SQL injection."
            },
            {
                "id": "weak_crypto",
                "name": "Weak Cryptography",
                "regex": r"(MD5|SHA1)\(",
                "severity": "MEDIUM",
                "description": "MD5 and SHA1 are considered cryptographically broken and should not be used for security-sensitive purposes."
            }
        ]

    def scan_file(self, file_path: str, content: str) -> List[Dict[str, Any]]:
        findings = []
        lines = content.split("\n")
        
        for pattern in self.patterns:
            matches = re.finditer(pattern["regex"], content)
            for match in matches:
                # Find line number
                line_no = content[:match.start()].count("\n") + 1
                findings.append({
                    "id": pattern["id"],
                    "name": pattern["name"],
                    "severity": pattern["severity"],
                    "description": pattern["description"],
                    "line": line_no,
                    "snippet": lines[line_no-1].strip()
                })
        return findings
