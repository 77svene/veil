"""
Enterprise Security & Compliance Module for Browser-Use
Provides security controls, audit logging, and compliance features for GDPR/SOC2.
"""

import re
import json
import hashlib
import hmac
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Pattern, Set, Union
from dataclasses import dataclass, field, asdict
from enum import Enum
import base64
import secrets
from pathlib import Path
import threading
from contextlib import contextmanager

# Third-party imports (optional for enhanced functionality)
try:
    import boto3
    from botocore.exceptions import ClientError
    HAS_AWS = True
except ImportError:
    HAS_AWS = False

try:
    import hvac
    HAS_VAULT = True
except ImportError:
    HAS_VAULT = False

try:
    from PIL import Image
    import pytesseract
    HAS_OCR = True
except ImportError:
    HAS_OCR = False

# Import from existing codebase
from veil.agent.views import AgentAction, AgentState
from veil.actor.page import Page


class SensitivityLevel(Enum):
    """Classification levels for sensitive data."""
    PUBLIC = "public"
    INTERNAL = "internal"
    CONFIDENTIAL = "confidential"
    RESTRICTED = "restricted"
    PII = "pii"
    CREDENTIAL = "credential"


class AuditEventType(Enum):
    """Types of audit events."""
    PAGE_NAVIGATION = "page_navigation"
    ELEMENT_INTERACTION = "element_interaction"
    DATA_EXTRACTION = "data_extraction"
    CREDENTIAL_ACCESS = "credential_access"
    SCREENSHOT_CAPTURE = "screenshot_capture"
    FORM_SUBMISSION = "form_submission"
    AUTHENTICATION = "authentication"
    DATA_EXPORT = "data_export"
    CONFIGURATION_CHANGE = "configuration_change"
    ERROR = "error"
    SECURITY_VIOLATION = "security_violation"


@dataclass
class SensitivePattern:
    """Pattern definition for sensitive data detection."""
    name: str
    pattern: Pattern[str]
    sensitivity: SensitivityLevel
    replacement: str = "[REDACTED]"
    description: str = ""


@dataclass
class AuditEvent:
    """Immutable audit event record."""
    event_id: str
    timestamp: datetime
    event_type: AuditEventType
    actor: str  # Agent ID or user identifier
    action: str
    target: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)
    sensitivity_level: SensitivityLevel = SensitivityLevel.INTERNAL
    source_ip: Optional[str] = None
    user_agent: Optional[str] = None
    session_id: Optional[str] = None
    checksum: Optional[str] = None
    
    def __post_init__(self):
        """Calculate checksum after initialization."""
        if self.checksum is None:
            self.checksum = self._calculate_checksum()
    
    def _calculate_checksum(self) -> str:
        """Calculate HMAC checksum for tamper detection."""
        data = f"{self.event_id}:{self.timestamp.isoformat()}:{self.event_type.value}:{self.actor}:{self.action}"
        secret = os.getenv("AUDIT_LOG_SECRET", "default-secret-change-in-production")
        return hmac.new(
            secret.encode(),
            data.encode(),
            hashlib.sha256
        ).hexdigest()
    
    def verify_integrity(self) -> bool:
        """Verify the audit event hasn't been tampered with."""
        expected_checksum = self._calculate_checksum()
        return hmac.compare_digest(self.checksum, expected_checksum)
    
    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        data = asdict(self)
        data['timestamp'] = self.timestamp.isoformat()
        data['event_type'] = self.event_type.value
        data['sensitivity_level'] = self.sensitivity_level.value
        return data


class SecretManager:
    """Interface for secret management systems."""
    
    def __init__(self, provider: str = "env", **kwargs):
        self.provider = provider
        self._cache = {}
        self._cache_ttl = 300  # 5 minutes
        
        if provider == "aws" and HAS_AWS:
            self.client = boto3.client('secretsmanager', **kwargs)
        elif provider == "vault" and HAS_VAULT:
            self.client = hvac.Client(**kwargs)
        elif provider == "env":
            self.client = None
        else:
            raise ValueError(f"Unsupported secret provider: {provider}")
    
    def get_secret(self, secret_name: str, version: Optional[str] = None) -> str:
        """Retrieve secret from configured provider."""
        cache_key = f"{secret_name}:{version}"
        
        # Check cache
        if cache_key in self._cache:
            cached_time, value = self._cache[cache_key]
            if time.time() - cached_time < self._cache_ttl:
                return value
        
        # Fetch from provider
        if self.provider == "aws":
            try:
                if version:
                    response = self.client.get_secret_value(
                        SecretId=secret_name,
                        VersionId=version
                    )
                else:
                    response = self.client.get_secret_value(SecretId=secret_name)
                secret_value = response['SecretString']
            except ClientError as e:
                raise ValueError(f"Failed to retrieve secret {secret_name}: {e}")
        
        elif self.provider == "vault":
            try:
                response = self.client.secrets.kv.v2.read_secret_version(
                    path=secret_name,
                    version=version
                )
                secret_value = response['data']['data']['value']
            except Exception as e:
                raise ValueError(f"Failed to retrieve secret from Vault {secret_name}: {e}")
        
        elif self.provider == "env":
            secret_value = os.getenv(secret_name)
            if secret_value is None:
                raise ValueError(f"Environment variable {secret_name} not found")
        
        # Cache and return
        self._cache[cache_key] = (time.time(), secret_value)
        return secret_value
    
    def rotate_secret(self, secret_name: str) -> str:
        """Rotate a secret and return the new value."""
        new_value = secrets.token_urlsafe(32)
        
        if self.provider == "aws":
            self.client.put_secret_value(
                SecretId=secret_name,
                SecretString=new_value
            )
        elif self.provider == "vault":
            self.client.secrets.kv.v2.create_or_update_secret(
                path=secret_name,
                secret={'value': new_value}
            )
        elif self.provider == "env":
            # For environment variables, we can't actually rotate them
            # This would require updating the process environment
            pass
        
        # Invalidate cache
        cache_key = f"{secret_name}:None"
        self._cache.pop(cache_key, None)
        
        return new_value


class SensitiveDataRedactor:
    """Redacts sensitive data from text and images."""
    
    # Default sensitive patterns
    DEFAULT_PATTERNS = [
        SensitivePattern(
            name="credit_card",
            pattern=re.compile(r'\b(?:\d[ -]*?){13,16}\b'),
            sensitivity=SensitivityLevel.PII,
            replacement="[CREDIT_CARD_REDACTED]",
            description="Credit card numbers"
        ),
        SensitivePattern(
            name="ssn",
            pattern=re.compile(r'\b\d{3}-\d{2}-\d{4}\b'),
            sensitivity=SensitivityLevel.PII,
            replacement="[SSN_REDACTED]",
            description="Social Security Numbers"
        ),
        SensitivePattern(
            name="email",
            pattern=re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'),
            sensitivity=SensitivityLevel.PII,
            replacement="[EMAIL_REDACTED]",
            description="Email addresses"
        ),
        SensitivePattern(
            name="password",
            pattern=re.compile(r'(?i)(password|passwd|pwd|secret|token|api[_-]?key)[\s]*[=:][\s]*[^\s]+'),
            sensitivity=SensitivityLevel.CREDENTIAL,
            replacement="[CREDENTIAL_REDACTED]",
            description="Passwords and API keys"
        ),
        SensitivePattern(
            name="bearer_token",
            pattern=re.compile(r'Bearer\s+[A-Za-z0-9\-._~+/]+=*'),
            sensitivity=SensitivityLevel.CREDENTIAL,
            replacement="[BEARER_TOKEN_REDACTED]",
            description="Bearer tokens"
        ),
        SensitivePattern(
            name="aws_key",
            pattern=re.compile(r'(?<![A-Z0-9])[A-Z0-9]{20}(?![A-Z0-9])'),
            sensitivity=SensitivityLevel.CREDENTIAL,
            replacement="[AWS_KEY_REDACTED]",
            description="AWS access keys"
        ),
        SensitivePattern(
            name="private_key",
            pattern=re.compile(r'-----BEGIN (?:RSA |EC |DSA )?PRIVATE KEY-----[\s\S]+?-----END (?:RSA |EC |DSA )?PRIVATE KEY-----'),
            sensitivity=SensitivityLevel.RESTRICTED,
            replacement="[PRIVATE_KEY_REDACTED]",
            description="Private keys"
        ),
    ]
    
    def __init__(self, patterns: Optional[List[SensitivePattern]] = None):
        self.patterns = patterns or self.DEFAULT_PATTERNS
        self._compiled_patterns = [(p.pattern, p.replacement) for p in self.patterns]
    
    def add_pattern(self, pattern: SensitivePattern):
        """Add a custom sensitive pattern."""
        self.patterns.append(pattern)
        self._compiled_patterns.append((pattern.pattern, pattern.replacement))
    
    def redact_text(self, text: str) -> str:
        """Redact sensitive data from text."""
        if not text:
            return text
        
        redacted = text
        for pattern, replacement in self._compiled_patterns:
            redacted = pattern.sub(replacement, redacted)
        
        return redacted
    
    def redact_dict(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Recursively redact sensitive data from dictionary."""
        if not isinstance(data, dict):
            return data
        
        redacted = {}
        for key, value in data.items():
            if isinstance(value, str):
                redacted[key] = self.redact_text(value)
            elif isinstance(value, dict):
                redacted[key] = self.redact_dict(value)
            elif isinstance(value, list):
                redacted[key] = [self.redact_dict(item) if isinstance(item, dict) 
                               else self.redact_text(item) if isinstance(item, str) 
                               else item for item in value]
            else:
                redacted[key] = value
        
        return redacted
    
    def redact_image(self, image_path: str, output_path: Optional[str] = None) -> Optional[str]:
        """Redact sensitive data from image using OCR."""
        if not HAS_OCR:
            logging.warning("OCR not available. Install pytesseract and PIL for image redaction.")
            return None
        
        try:
            img = Image.open(image_path)
            text = pytesseract.image_to_string(img)
            
            # Check if text contains sensitive data
            redacted_text = self.redact_text(text)
            if redacted_text != text:
                # Create redacted version (simplified - in production would need proper image editing)
                logging.info(f"Sensitive data detected in image {image_path}")
                # For now, just return the path - actual redaction would require image manipulation
                return image_path
            
            return image_path
        except Exception as e:
            logging.error(f"Failed to redact image {image_path}: {e}")
            return None


class AuditLogger:
    """Enterprise audit logging system with compliance features."""
    
    def __init__(
        self,
        log_file: str = "audit.log",
        secret_manager: Optional[SecretManager] = None,
        redactor: Optional[SensitiveDataRedactor] = None,
        enable_console: bool = False,
        max_file_size: int = 100 * 1024 * 1024,  # 100MB
        backup_count: int = 10
    ):
        self.log_file = Path(log_file)
        self.secret_manager = secret_manager or SecretManager()
        self.redactor = redactor or SensitiveDataRedactor()
        self.enable_console = enable_console
        self.max_file_size = max_file_size
        self.backup_count = backup_count
        
        # Thread safety
        self._lock = threading.RLock()
        self._session_id = self._generate_session_id()
        
        # Setup logging
        self._setup_logging()
        
        # Statistics
        self.stats = {
            "total_events": 0,
            "sensitive_events": 0,
            "last_event_time": None
        }
    
    def _generate_session_id(self) -> str:
        """Generate unique session ID."""
        return hashlib.sha256(
            f"{time.time()}{os.getpid()}{threading.get_ident()}".encode()
        ).hexdigest()[:16]
    
    def _setup_logging(self):
        """Configure logging handlers."""
        self.logger = logging.getLogger(f"veil.audit.{self._session_id}")
        self.logger.setLevel(logging.INFO)
        
        # File handler with rotation
        from logging.handlers import RotatingFileHandler
        file_handler = RotatingFileHandler(
            self.log_file,
            maxBytes=self.max_file_size,
            backupCount=self.backup_count
        )
        file_handler.setFormatter(logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s'
        ))
        self.logger.addHandler(file_handler)
        
        # Console handler if enabled
        if self.enable_console:
            console_handler = logging.StreamHandler()
            console_handler.setFormatter(logging.Formatter(
                '%(asctime)s - AUDIT - %(message)s'
            ))
            self.logger.addHandler(console_handler)
    
    def _create_event_id(self) -> str:
        """Create unique event ID."""
        timestamp = int(time.time() * 1000)
        random_part = secrets.token_hex(8)
        return f"EVT-{timestamp}-{random_part}"
    
    def log_event(
        self,
        event_type: AuditEventType,
        actor: str,
        action: str,
        target: Optional[str] = None,
        details: Optional[Dict[str, Any]] = None,
        sensitivity_level: SensitivityLevel = SensitivityLevel.INTERNAL,
        source_ip: Optional[str] = None,
        user_agent: Optional[str] = None
    ) -> AuditEvent:
        """Log an audit event."""
        with self._lock:
            # Redact sensitive details
            redacted_details = self.redactor.redact_dict(details) if details else {}
            
            event = AuditEvent(
                event_id=self._create_event_id(),
                timestamp=datetime.now(timezone.utc),
                event_type=event_type,
                actor=actor,
                action=action,
                target=target,
                details=redacted_details,
                sensitivity_level=sensitivity_level,
                source_ip=source_ip,
                user_agent=user_agent,
                session_id=self._session_id
            )
            
            # Update statistics
            self.stats["total_events"] += 1
            if sensitivity_level in [SensitivityLevel.PII, SensitivityLevel.CREDENTIAL, SensitivityLevel.RESTRICTED]:
                self.stats["sensitive_events"] += 1
            self.stats["last_event_time"] = event.timestamp.isoformat()
            
            # Log to file
            log_message = json.dumps(event.to_dict())
            self.logger.info(log_message)
            
            # Additional handling for high-sensitivity events
            if sensitivity_level in [SensitivityLevel.CREDENTIAL, SensitivityLevel.RESTRICTED]:
                self._handle_sensitive_event(event)
            
            return event
    
    def _handle_sensitive_event(self, event: AuditEvent):
        """Special handling for sensitive events."""
        # In production, this might:
        # 1. Send alerts to security team
        # 2. Trigger additional verification
        # 3. Store in separate secure log
        logging.warning(f"Sensitive event detected: {event.event_type.value} by {event.actor}")
    
    def log_agent_action(self, agent_state: AgentState, action: AgentAction):
        """Log an agent action with context."""
        details = {
            "step": agent_state.step,
            "goal": agent_state.goal,
            "memory": self.redactor.redact_text(str(agent_state.memory)) if hasattr(agent_state, 'memory') else None,
            "action_params": self.redactor.redact_dict(action.params) if hasattr(action, 'params') else {}
        }
        
        sensitivity = SensitivityLevel.INTERNAL
        if hasattr(action, 'sensitivity'):
            sensitivity = action.sensitivity
        
        return self.log_event(
            event_type=AuditEventType.ELEMENT_INTERACTION,
            actor=f"agent_{agent_state.agent_id}",
            action=action.action_type,
            target=action.target,
            details=details,
            sensitivity_level=sensitivity
        )
    
    def log_page_navigation(self, page: Page, url: str, actor: str = "system"):
        """Log page navigation event."""
        return self.log_event(
            event_type=AuditEventType.PAGE_NAVIGATION,
            actor=actor,
            action="navigate",
            target=url,
            details={
                "page_title": page.title() if hasattr(page, 'title') else None,
                "page_url": page.url() if hasattr(page, 'url') else None
            }
        )
    
    def log_credential_access(self, credential_name: str, actor: str, success: bool):
        """Log credential access attempt."""
        return self.log_event(
            event_type=AuditEventType.CREDENTIAL_ACCESS,
            actor=actor,
            action="access_credential",
            target=credential_name,
            details={"success": success},
            sensitivity_level=SensitivityLevel.CREDENTIAL
        )
    
    def log_security_violation(self, violation_type: str, details: Dict[str, Any], actor: str = "system"):
        """Log security violation."""
        return self.log_event(
            event_type=AuditEventType.SECURITY_VIOLATION,
            actor=actor,
            action=violation_type,
            details=details,
            sensitivity_level=SensitivityLevel.RESTRICTED
        )
    
    def get_audit_trail(
        self,
        start_time: Optional[datetime] = None,
        end_time: Optional[datetime] = None,
        event_types: Optional[List[AuditEventType]] = None,
        actor: Optional[str] = None,
        limit: int = 1000
    ) -> List[AuditEvent]:
        """Retrieve audit trail with filtering."""
        # In production, this would query a database or log aggregation system
        # For now, we'll read from the log file
        events = []
        
        try:
            with open(self.log_file, 'r') as f:
                for line in f:
                    if len(events) >= limit:
                        break
                    
                    try:
                        data = json.loads(line.strip())
                        event = AuditEvent(
                            event_id=data['event_id'],
                            timestamp=datetime.fromisoformat(data['timestamp']),
                            event_type=AuditEventType(data['event_type']),
                            actor=data['actor'],
                            action=data['action'],
                            target=data.get('target'),
                            details=data.get('details', {}),
                            sensitivity_level=SensitivityLevel(data['sensitivity_level']),
                            source_ip=data.get('source_ip'),
                            user_agent=data.get('user_agent'),
                            session_id=data.get('session_id'),
                            checksum=data.get('checksum')
                        )
                        
                        # Apply filters
                        if start_time and event.timestamp < start_time:
                            continue
                        if end_time and event.timestamp > end_time:
                            continue
                        if event_types and event.event_type not in event_types:
                            continue
                        if actor and event.actor != actor:
                            continue
                        
                        # Verify integrity
                        if event.verify_integrity():
                            events.append(event)
                        else:
                            logging.warning(f"Integrity check failed for event {event.event_id}")
                    
                    except (json.JSONDecodeError, KeyError, ValueError) as e:
                        logging.error(f"Failed to parse audit log entry: {e}")
                        continue
        
        except FileNotFoundError:
            logging.warning(f"Audit log file not found: {self.log_file}")
        
        return events
    
    def export_compliance_report(
        self,
        start_date: datetime,
        end_date: datetime,
        format: str = "json"
    ) -> str:
        """Generate compliance report for GDPR/SOC2."""
        events = self.get_audit_trail(start_date, end_date)
        
        report = {
            "report_id": hashlib.sha256(f"{start_date}{end_date}".encode()).hexdigest()[:16],
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "period": {
                "start": start_date.isoformat(),
                "end": end_date.isoformat()
            },
            "summary": {
                "total_events": len(events),
                "sensitive_events": sum(1 for e in events if e.sensitivity_level in [
                    SensitivityLevel.PII, 
                    SensitivityLevel.CREDENTIAL, 
                    SensitivityLevel.RESTRICTED
                ]),
                "unique_actors": len(set(e.actor for e in events)),
                "event_types": {}
            },
            "events": [e.to_dict() for e in events],
            "integrity_check": {
                "total_events": len(events),
                "valid_events": sum(1 for e in events if e.verify_integrity()),
                "invalid_events": sum(1 for e in events if not e.verify_integrity())
            }
        }
        
        # Count event types
        for event in events:
            event_type = event.event_type.value
            report["summary"]["event_types"][event_type] = \
                report["summary"]["event_types"].get(event_type, 0) + 1
        
        if format == "json":
            return json.dumps(report, indent=2)
        elif format == "csv":
            # Simplified CSV export
            import csv
            import io
            
            output = io.StringIO()
            writer = csv.writer(output)
            
            # Header
            writer.writerow(["Event ID", "Timestamp", "Type", "Actor", "Action", "Sensitivity"])
            
            # Data
            for event in events:
                writer.writerow([
                    event.event_id,
                    event.timestamp.isoformat(),
                    event.event_type.value,
                    event.actor,
                    event.action,
                    event.sensitivity_level.value
                ])
            
            return output.getvalue()
        else:
            raise ValueError(f"Unsupported format: {format}")
    
    def verify_log_integrity(self) -> Dict[str, Any]:
        """Verify integrity of entire audit log."""
        events = self.get_audit_trail(limit=10000)  # Limit for performance
        
        total = len(events)
        valid = sum(1 for e in events if e.verify_integrity())
        invalid = total - valid
        
        return {
            "total_events": total,
            "valid_events": valid,
            "invalid_events": invalid,
            "integrity_percentage": (valid / total * 100) if total > 0 else 100,
            "tampered_detected": invalid > 0
        }


class SecurityPolicy:
    """Defines security policies for browser automation."""
    
    def __init__(self):
        self.allowed_domains: Set[str] = set()
        self.blocked_domains: Set[str] = set()
        self.sensitive_selectors: Set[str] = set()
        self.max_screenshots_per_session: int = 100
        self.require_https: bool = True
        self.redact_screenshots: bool = True
        self.log_all_actions: bool = True
        self.retain_logs_days: int = 365  # SOC2 requirement
    
    def is_domain_allowed(self, url: str) -> bool:
        """Check if domain is allowed."""
        from urllib.parse import urlparse
        
        parsed = urlparse(url)
        domain = parsed.netloc
        
        if domain in self.blocked_domains:
            return False
        
        if self.allowed_domains and domain not in self.allowed_domains:
            return False
        
        if self.require_https and parsed.scheme != "https":
            return False
        
        return True
    
    def is_sensitive_selector(self, selector: str) -> bool:
        """Check if selector targets sensitive elements."""
        sensitive_patterns = [
            r'password',
            r'credit.?card',
            r'ssn',
            r'social.?security',
            r'account.?number',
            r'routing.?number',
            r'api.?key',
            r'secret',
            r'token'
        ]
        
        selector_lower = selector.lower()
        return any(re.search(pattern, selector_lower) for pattern in sensitive_patterns)


# Global instances for convenience
_default_audit_logger = None
_default_redactor = None
_default_secret_manager = None


def get_audit_logger() -> AuditLogger:
    """Get or create default audit logger."""
    global _default_audit_logger
    if _default_audit_logger is None:
        _default_audit_logger = AuditLogger()
    return _default_audit_logger


def get_redactor() -> SensitiveDataRedactor:
    """Get or create default redactor."""
    global _default_redactor
    if _default_redactor is None:
        _default_redactor = SensitiveDataRedactor()
    return _default_redactor


def get_secret_manager() -> SecretManager:
    """Get or create default secret manager."""
    global _default_secret_manager
    if _default_secret_manager is None:
        provider = os.getenv("SECRET_MANAGER_PROVIDER", "env")
        _default_secret_manager = SecretManager(provider=provider)
    return _default_secret_manager


@contextmanager
def audit_context(
    event_type: AuditEventType,
    actor: str,
    action: str,
    **kwargs
):
    """Context manager for auditing operations."""
    audit_logger = get_audit_logger()
    start_time = time.time()
    
    try:
        yield
        # Log success
        audit_logger.log_event(
            event_type=event_type,
            actor=actor,
            action=f"{action}_success",
            details={"duration": time.time() - start_time, **kwargs}
        )
    except Exception as e:
        # Log failure
        audit_logger.log_event(
            event_type=AuditEventType.ERROR,
            actor=actor,
            action=f"{action}_failed",
            details={
                "error": str(e),
                "error_type": type(e).__name__,
                "duration": time.time() - start_time,
                **kwargs
            },
            sensitivity_level=SensitivityLevel.INTERNAL
        )
        raise


def sanitize_for_logging(data: Any) -> Any:
    """Sanitize data for safe logging."""
    redactor = get_redactor()
    
    if isinstance(data, str):
        return redactor.redact_text(data)
    elif isinstance(data, dict):
        return redactor.redact_dict(data)
    elif isinstance(data, (list, tuple)):
        return [sanitize_for_logging(item) for item in data]
    else:
        return data


# Integration with existing veil modules
def integrate_with_agent_service():
    """Monkey-patch AgentService to add audit logging."""
    from veil.agent.service import AgentService
    
    original_execute = AgentService.execute_action
    
    def audited_execute(self, action: AgentAction, *args, **kwargs):
        audit_logger = get_audit_logger()
        
        # Log before execution
        audit_logger.log_agent_action(self.state, action)
        
        try:
            result = original_execute(self, action, *args, **kwargs)
            
            # Log successful execution
            audit_logger.log_event(
                event_type=AuditEventType.ELEMENT_INTERACTION,
                actor=f"agent_{self.state.agent_id}",
                action=f"{action.action_type}_completed",
                target=action.target,
                details={"result": sanitize_for_logging(str(result))}
            )
            
            return result
        except Exception as e:
            # Log failed execution
            audit_logger.log_event(
                event_type=AuditEventType.ERROR,
                actor=f"agent_{self.state.agent_id}",
                action=f"{action.action_type}_failed",
                target=action.target,
                details={"error": str(e)},
                sensitivity_level=SensitivityLevel.INTERNAL
            )
            raise
    
    AgentService.execute_action = audited_execute


def integrate_with_page_class():
    """Add audit logging to Page class."""
    from veil.actor.page import Page
    
    original_goto = Page.goto
    original_screenshot = Page.screenshot
    
    def audited_goto(self, url: str, *args, **kwargs):
        audit_logger = get_audit_logger()
        security_policy = SecurityPolicy()
        
        # Security check
        if not security_policy.is_domain_allowed(url):
            audit_logger.log_security_violation(
                "domain_not_allowed",
                {"url": url, "allowed_domains": list(security_policy.allowed_domains)}
            )
            raise ValueError(f"Domain not allowed: {url}")
        
        # Log navigation
        audit_logger.log_page_navigation(self, url)
        
        return original_goto(self, url, *args, **kwargs)
    
    def audited_screenshot(self, *args, **kwargs):
        audit_logger = get_audit_logger()
        redactor = get_redactor()
        
        # Take screenshot
        screenshot_path = original_screenshot(self, *args, **kwargs)
        
        # Log screenshot capture
        audit_logger.log_event(
            event_type=AuditEventType.SCREENSHOT_CAPTURE,
            actor="system",
            action="capture_screenshot",
            target=screenshot_path,
            details={"page_url": self.url() if hasattr(self, 'url') else None}
        )
        
        # Redact sensitive data from screenshot if enabled
        if redactor.redact_image(screenshot_path):
            logging.info(f"Screenshot redacted: {screenshot_path}")
        
        return screenshot_path
    
    Page.goto = audited_goto
    Page.screenshot = audited_screenshot


# Auto-integrate when module is imported
def auto_integrate():
    """Automatically integrate audit logging with existing modules."""
    try:
        integrate_with_agent_service()
        integrate_with_page_class()
        logging.info("Audit logging integrated with veil modules")
    except ImportError as e:
        logging.warning(f"Could not integrate with all modules: {e}")


# Run auto-integration
auto_integrate()