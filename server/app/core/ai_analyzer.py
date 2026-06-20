import os
import json
import re
from typing import List, Dict, Any, Callable, Optional
from openai import OpenAI, APIError, APIConnectionError, RateLimitError

class AIAnalyzer:
    def __init__(self):
        # Use NVIDIA NIM API keys for all analysis
        nim_keys_env = os.getenv("NIM_API_KEYS", os.getenv("NIM_API_KEY", ""))
        self.nim_keys = [k.strip() for k in nim_keys_env.split(",") if k.strip()]

        if not self.nim_keys:
            raise ValueError("No API keys found: NIM_API_KEYS required")

        print(f"Initialized AIAnalyzer with {len(self.nim_keys)} NIM keys")
        # Use NIM for both analysis and chat
        self.analysis_model = "nvidia/llama-3.3-nemotron-super-49b-v1"  # Using NIM's capable model for analysis
        self.chat_model = "nvidia/llama-3.3-nemotron-super-49b-v1"  # NVIDIA's chat model
        self.analysis_base_url = "https://integrate.api.nvidia.com/v1"
        self.chat_base_url = "https://integrate.api.nvidia.com/v1"
        self._progress_callback: Optional[Callable[[str], None]] = None

    def set_progress_callback(self, callback: Callable[[str], None]):
        """Set a callback function to report progress to the frontend."""
        self._progress_callback = callback

    def _report_progress(self, message: str):
        """Report progress both to console and to the callback."""
        print(message)
        if self._progress_callback:
            self._progress_callback(message)

    def _get_nim_client(self, key: str) -> OpenAI:
        """Create a new OpenAI client for NVIDIA NIM API."""
        return OpenAI(api_key=key, base_url=self.analysis_base_url)

    def _strip_markdown_codeblocks(self, text: str) -> str:
        """Remove markdown code block wrappers from LLM responses."""
        text = text.strip()
        # Match ```json ... ``` or ``` ... ```
        pattern = r'^```(?:json)?\s*\n?(.*?)\n?\s*```$'
        match = re.match(pattern, text, re.DOTALL)
        if match:
            return match.group(1).strip()
        return text

    def _safe_int(self, value, default=0):
        """Try to coerce a value to int, with safe fallbacks."""
        try:
            return int(value)
        except Exception:
            # Try to extract first integer from string
            try:
                s = str(value)
                m = re.search(r"(\d+)", s)
                if m:
                    return int(m.group(1))
            except Exception:
                pass
        return default

    def _safe_parse_json(self, text: str) -> Any:
        """Parse JSON from LLM response, handling markdown wrappers and common issues."""
        cleaned = self._strip_markdown_codeblocks(text)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            # Try to find JSON object or array in the text
            # Look for the first { ... } or [ ... ]
            for start_char, end_char in [('{', '}'), ('[', ']')]:
                start = cleaned.find(start_char)
                if start == -1:
                    continue
                depth = 0
                for i in range(start, len(cleaned)):
                    if cleaned[i] == start_char:
                        depth += 1
                    elif cleaned[i] == end_char:
                        depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(cleaned[start:i+1])
                        except json.JSONDecodeError:
                            break
            raise

    def analyze_codebase(self, files: List[Dict], repo_info: str = "") -> Dict[str, Any]:
        """Analyze codebase for hotspots, vulnerabilities, and insights using AI."""
        try:
            # Prepare code summary for analysis
            code_summary = self._prepare_code_summary(files)

            if not code_summary or code_summary == "Complex codebase analysis":
                self._report_progress("Code summary empty, skipping AI analysis")
                return {
                    "vulnerabilities": [],
                    "hotspots": [],
                    "recommendations": ""
                }

            # Analyze with NIM
            self._report_progress("🔍 Starting comprehensive AI scan (tech stack, dependencies, security)...")
            comp_result = self._run_comprehensive_scan(code_summary)
            vulnerabilities = comp_result.get("vulnerabilities", [])
            tech_stack = comp_result.get("tech_stack", {})
            dependencies = comp_result.get("dependencies", {})

            self._report_progress("🔍 Starting AI-powered hotspot analysis...")
            hotspots = self._identify_hotspots(files, code_summary)

            return {
                "vulnerabilities": vulnerabilities,
                "hotspots": hotspots,
                "tech_stack": tech_stack,
                "dependencies": dependencies,
                "recommendations": ""
            }
        except (APIError, APIConnectionError, RateLimitError) as e:
            self._report_progress(f"⚠️ NIM API error: {e}. Continuing with basic analysis.")
            return {
                "vulnerabilities": [],
                "hotspots": [],
                "tech_stack": {},
                "dependencies": {},
                "recommendations": ""
            }
        except Exception as e:
            self._report_progress(f"⚠️ Unexpected error in AI analysis: {e}. Continuing with basic analysis.")
            return {
                "vulnerabilities": [],
                "hotspots": [],
                "tech_stack": {},
                "dependencies": {},
                "recommendations": ""
            }

    def _prepare_code_summary(self, files: List[Dict]) -> str:
        """Create a summary of the codebase for analysis."""
        summary_parts = []
        file_count = 0

        for file in files[:50]:  # Limit to first 50 files for token efficiency
            if file_count >= 20:  # Analyze max 20 files in detail
                break

            file_path = file.get("file_path", "")
            language = file.get("language", "")
            functions = file.get("functions", [])
            complexity = file.get("complexity", 0)
            content = str(file.get("content", ""))
            content_preview = "\n".join(content.splitlines()[:20])

            function_names = []
            for fn in functions[:5]:
                if isinstance(fn, dict):
                    function_names.append(fn.get("name", "unknown"))
                else:
                    function_names.append(str(fn))

            summary_parts.append(f"""
File: {file_path} ({language})
Complexity: {complexity}
Functions: {', '.join(function_names) if function_names else 'N/A'}
Snippet:
{content_preview[:1200]}
""")
            file_count += 1

        return "\n".join(summary_parts) if summary_parts else "Complex codebase analysis"

    def _run_comprehensive_scan(self, code_summary: str) -> Dict[str, Any]:
        """Use AI to identify tech stack, dependencies, and security vulnerabilities."""
        if not code_summary or len(code_summary.strip()) < 10:
            self._report_progress("Code summary too short, skipping comprehensive analysis")
            return {"vulnerabilities": [], "tech_stack": {}, "dependencies": {}}

        # Read the NIM analysis prompt
        prompt_path = os.path.join(os.path.dirname(__file__), "..", "..", "prompts", "nim_analysis_prompt.txt")
        try:
            with open(prompt_path, "r") as f:
                base_prompt = f.read()
        except FileNotFoundError:
            self._report_progress("⚠️ NIM prompt file not found, using fallback")
            base_prompt = "Analyze the following codebase for security vulnerabilities and code quality issues."

        prompt = f"""{base_prompt}

Code Summary:
{code_summary}

Focus on identifying security vulnerabilities, outdated dependencies, and code quality issues.
Return ONLY valid JSON matching the structure specified in the prompt guidelines."""

        # Try NIM API
        result = self._try_nim_analysis(prompt, "comprehensive")
        if result is not None:
            return result

        self._report_progress("⚠️ NIM analysis did not return comprehensive data")
        return {"vulnerabilities": [], "tech_stack": {}, "dependencies": {}}

    def _extract_vulnerabilities_from_response(self, parsed: Any) -> List[Dict]:
        """Extract vulnerability list from various response formats."""
        # If it's already a list of vulnerability objects
        if isinstance(parsed, list):
            clean_findings = []
            for f in parsed:
                if isinstance(f, dict):
                    clean_findings.append({
                        "name": str(f.get("name", f.get("type", "Unknown Issue"))),
                        "severity": str(f.get("severity", "MEDIUM")),
                        "description": str(f.get("description", "")),
                        "file_path": str(f.get("file_path", f.get("location", ""))),
                        "line": self._safe_int(f.get("line", 0)),
                        "snippet": str(f.get("snippet", ""))
                    })
            return clean_findings

        # If it's a full analysis object, extract security_risks
        if isinstance(parsed, dict):
            vulnerabilities = []
            # Try security_risks key (from our prompt format)
            for key in ["security_risks", "vulnerabilities", "vulnerable"]:
                items = parsed.get(key, [])
                if isinstance(items, list):
                    for f in items:
                        if isinstance(f, dict):
                            # Parse location into file_path and line
                            location = str(f.get("location", f.get("file_path", "")))
                            file_path = location.split(":")[0] if ":" in location else location
                            line = 0
                            if ":" in location:
                                line_candidate = location.split(":")[-1]
                                line = self._safe_int(line_candidate, 0)
                            vulnerabilities.append({
                                "name": str(f.get("name", f.get("type", "Unknown Issue"))),
                                "severity": str(f.get("severity", "MEDIUM")),
                                "description": str(f.get("description", "")),
                                "file_path": file_path,
                                "line": line,
                                "snippet": str(f.get("snippet", ""))
                            })
                    if vulnerabilities:
                        break

            # Also check dependencies.vulnerable
            deps = parsed.get("dependencies", {})
            if isinstance(deps, dict):
                for vuln_dep in deps.get("vulnerable", []):
                    if isinstance(vuln_dep, dict):
                        vulnerabilities.append({
                            "name": str(vuln_dep.get("name", "Unknown Package")),
                            "severity": str(vuln_dep.get("severity", "MEDIUM")),
                            "description": f"Vulnerable dependency: {vuln_dep.get('vulnerability', 'Unknown CVE')}",
                            "file_path": "package.json",
                            "line": 0,
                            "snippet": ""
                        })

            return vulnerabilities

        return []

    def _extract_hotspots_from_response(self, parsed: Any) -> List[Dict]:
        """Extract hotspot list from various response formats."""
        items = []

        # If it's already a list
        if isinstance(parsed, list):
            items = parsed
        # If it's an object, try to extract the hotspots key
        elif isinstance(parsed, dict):
            for key in ["hotspots", "code_hotspots", "complexity_hotspots"]:
                candidate = parsed.get(key, [])
                if isinstance(candidate, list) and candidate:
                    items = candidate
                    break

        formatted = []
        for item in items:
            if isinstance(item, dict):
                formatted.append({
                    "file_path": str(item.get("file_path", "")),
                    "complexity": self._safe_int(item.get("complexity", item.get("complexity_score", 0))),
                    "language": item.get("language", "unknown"),
                    "functions": [],
                    "classes": [],
                    "reason": str(item.get("reason", "")),
                    "suggestions": item.get("suggestions", [])
                })

        return formatted

    def _try_nim_analysis(self, prompt: str, analysis_type: str) -> Optional[Any]:
        """Try to analyze using NVIDIA NIM API. Only retries on API errors, not parse failures."""
        last_api_error = None

        for idx, key in enumerate(self.nim_keys):
            try:
                self._report_progress(f"🔑 Attempting {analysis_type} analysis with NIM key {idx + 1}/{len(self.nim_keys)}")
                client = self._get_nim_client(key)

                response = client.chat.completions.create(
                    model=self.analysis_model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.3,
                    max_tokens=2000
                )

                # Handle different response formats from NIM API
                if isinstance(response, str):
                    result_text = response.strip()
                elif hasattr(response, 'choices') and response.choices:
                    result_text = response.choices[0].message.content.strip()
                else:
                    result_text = str(response).strip()

                self._report_progress(f"✅ Successfully got {analysis_type} response from NIM (length: {len(result_text)})")

                # Parse the JSON response
                try:
                    parsed = self._safe_parse_json(result_text)
                except (json.JSONDecodeError, ValueError) as e:
                    self._report_progress(f"⚠️ Failed to parse NIM {analysis_type} response as JSON: {e}")
                    # Don't retry other keys for parse failures — the API worked, the output was bad
                    return []

                # Extract the appropriate data based on analysis type
                if analysis_type == "comprehensive":
                    vulns = self._extract_vulnerabilities_from_response(parsed)
                    tech_stack = parsed.get("tech_stack", {}) if isinstance(parsed, dict) else {}
                    dependencies = parsed.get("dependencies", {}) if isinstance(parsed, dict) else {}
                    self._report_progress(f"✅ Extracted tech stack, dependencies, and {len(vulns)} vulnerabilities from NIM response")
                    return {
                        "vulnerabilities": vulns,
                        "tech_stack": tech_stack,
                        "dependencies": dependencies
                    }
                else:
                    results = self._extract_hotspots_from_response(parsed)
                    self._report_progress(f"✅ Extracted {len(results)} {analysis_type} from NIM response")
                    return results

            except (APIError, APIConnectionError, RateLimitError) as e:
                last_api_error = e
                self._report_progress(f"❌ NIM API error with key {idx + 1}: {e}")
                continue  # Try next key only for API errors
            except Exception as e:
                self._report_progress(f"❌ Unexpected error with NIM key {idx + 1}: {e}")
                last_api_error = e
                continue

        if last_api_error:
            self._report_progress(f"❌ All NIM API keys failed for {analysis_type}. Last error: {last_api_error}")
        return None


    def analyze_with_nim_chat(self, question: str, repo_context: str = "") -> str:
        """Use NVIDIA NIM API for conversational analysis of the repository."""
        try:
            from openai import OpenAI
        except ImportError:
            print("OpenAI package not installed, skipping NIM chat")
            return "I'm unable to access the conversational analysis feature at the moment."

        # Read the NIM analysis prompt for conversational context
        prompt_path = os.path.join(os.path.dirname(__file__), "..", "..", "prompts", "nim_chat_prompt.txt")
        try:
            with open(prompt_path, "r") as f:
                base_prompt = f.read()
        except FileNotFoundError:
            print("NIM prompt file not found, using fallback")
            base_prompt = "You are an expert AI assistant specialized in conversational code analysis."

        prompt = f"""{base_prompt}

Repository Context:
{repo_context}

User Question: {question}

Provide a helpful, accurate response based on the repository analysis. If you don't have enough information to answer fully, say so and suggest what additional information would be helpful."""

        for idx, key in enumerate(self.nim_keys):
            try:
                print(f"Attempting conversational analysis with NIM key {idx + 1}/{len(self.nim_keys)}")
                client = self._get_nim_client(key)

                response = client.chat.completions.create(
                    model=self.chat_model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.3,
                    max_tokens=1500
                )

                result_text = response.choices[0].message.content.strip()
                print(f"Successfully got conversational response from NIM (length: {len(result_text)})")

                return result_text

            except Exception as e:
                print(f"NIM chat error with key {idx + 1}: {e}")
                continue

        return "I'm experiencing technical difficulties with the conversational analysis feature. Please try again later."

    def _identify_hotspots(self, files: List[Dict], code_summary: str) -> List[Dict]:
        """Use AI to identify code hotspots and complex areas."""
        if not code_summary or len(code_summary.strip()) < 10:
            self._report_progress("Code summary too short, skipping hotspot analysis")
            return []

        # Read the NIM analysis prompt for hotspots section
        prompt_path = os.path.join(os.path.dirname(__file__), "..", "..", "prompts", "nim_analysis_prompt.txt")
        try:
            with open(prompt_path, "r") as f:
                base_prompt = f.read()
        except FileNotFoundError:
            self._report_progress("⚠️ NIM prompt file not found, using fallback")
            base_prompt = "Analyze the following codebase for code hotspots and complexity issues."

        prompt = f"""{base_prompt}

Files in codebase:
{json.dumps([{'path': f.get('file_path'), 'complexity': f.get('complexity', 0), 'language': f.get('language')} for f in files[:30]], indent=2)}

Code Summary:
{code_summary}

Focus on identifying code hotspots, complex functions, and areas needing refactoring.
Return ONLY valid JSON matching the structure specified in the prompt guidelines."""

        # Try NIM API — only retries on API errors
        result = self._try_nim_analysis(prompt, "hotspots")
        if result is not None:
            return result

        self._report_progress("⚠️ NIM analysis did not return hotspot data")
        return []

    def _generate_recommendations(self, code_summary: str) -> str:
        """Generate high-level recommendations for codebase improvement."""
        prompt = f"""Based on the following codebase analysis, provide 2-3 brief recommendations for improvement.
Be specific and actionable.

Code Summary:
{code_summary}

Provide concise recommendations (under 500 characters total)."""

        try:
            # Use NIM client for recommendations
            from openai import OpenAI
            client = OpenAI(api_key=self.nim_keys[0] if self.nim_keys else "", base_url=self.analysis_base_url)
            response = client.chat.completions.create(
                model=self.analysis_model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=300
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            print(f"Error generating recommendations: {e}")
            return ""
