"""
Enterprise Security & Compliance Module for Browser-Use
Provides: secret management, data redaction, audit logging, and compliance controls
"""

import os
import re
import json
import hashlib
import hmac
import logging
import time
import base64
import uuid
from typing import Any, Dict, List, Optional, Pattern, Set, Tuple, Union
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from abc import ABC, abstractmethod
import threading
from functools import wraps

# Optional imports for cloud providers
try:
    import boto3
    from botocore.exceptions import ClientError
    AWS_AVAILABLE = True
except ImportError:
    AWS_AVAILABLE = False

try:
    import hvac
    HASHICORP_AVAILABLE = True
except ImportError:
    HASHICORP_AVAILABLE = False

# Configure logging
logger = logging.getLogger(__name__)


class ComplianceStandard(Enum):
    """Supported compliance standards"""
    GDPR = "gdpr"
    SOC2 = "soc2"
    HIPAA = "hipaa"
    PCI_DSS = "pci_dss"


class SecretBackend(Enum):
    """Supported secret management backends"""
    ENVIRONMENT = "environment"
    AWS_SECRETS_MANAGER = "aws_secrets_manager"
    HASHICORP_VAULT = "hashicorp_vault"
    LOCAL_FILE = "local_file"


@dataclass
class AuditEvent:
    """Audit log event structure"""
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    event_type: str = ""
    actor: str = "veil"
    action: str = ""
    resource: str = ""
    details: Dict[str, Any] = field(default_factory=dict)
    ip_address: Optional[str] = None
    user_agent: Optional[str] = None
    session_id: Optional[str] = None
    compliance_tags: List[str] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class SensitiveDataPattern:
    """Patterns for detecting sensitive data"""
    
    # Common sensitive data patterns
    PATTERNS = {
        "email": re.compile(r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b'),
        "ssn": re.compile(r'\b\d{3}-\d{2}-\d{4}\b'),
        "credit_card": re.compile(r'\b(?:\d[ -]*?){13,16}\b'),
        "phone": re.compile(r'\b(?:\+?1[-.]?)?\(?([0-9]{3})\)?[-.]?([0-9]{3})[-.]?([0-9]{4})\b'),
        "ip_address": re.compile(r'\b(?:\d{1,3}\.){3}\d{1,3}\b'),
        "api_key": re.compile(r'\b[A-Za-z0-9]{32,}\b'),
        "password_field": re.compile(r'password["\']?\s*[:=]\s*["\']?([^"\']+)["\']?', re.IGNORECASE),
        "token": re.compile(r'\b(?:token|bearer|jwt)\s*[:=]\s*["\']?([^"\']+)["\']?', re.IGNORECASE),
    }
    
    @classmethod
    def detect(cls, text: str) -> List[Tuple[str, str, int, int]]:
        """Detect sensitive data in text"""
        detections = []
        for pattern_name, pattern in cls.PATTERNS.items():
            for match in pattern.finditer(text):
                detections.append((pattern_name, match.group(), match.start(), match.end()))
        return detections


class DataRedactor:
    """Automatic redaction of sensitive data"""
    
    def __init__(self, custom_patterns: Optional[Dict[str, Pattern]] = None):
        self.patterns = SensitiveDataPattern.PATTERNS.copy()
        if custom_patterns:
            self.patterns.update(custom_patterns)
        
        # Default redaction strings
        self.redaction_map = {
            "email": "[EMAIL_REDACTED]",
            "ssn": "[SSN_REDACTED]",
            "credit_card": "[CC_REDACTED]",
            "phone": "[PHONE_REDACTED]",
            "ip_address": "[IP_REDACTED]",
            "api_key": "[API_KEY_REDACTED]",
            "password_field": "[PASSWORD_REDACTED]",
            "token": "[TOKEN_REDACTED]",
        }
    
    def redact_text(self, text: str) -> str:
        """Redact sensitive data from text"""
        if not text:
            return text
            
        redacted = text
        for pattern_name, pattern in self.patterns.items():
            redaction = self.redaction_map.get(pattern_name, "[REDACTED]")
            redacted = pattern.sub(redaction, redacted)
        return redacted
    
    def redact_dict(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Redact sensitive data from dictionary"""
        redacted = {}
        for key, value in data.items():
            if isinstance(value, str):
                redacted[key] = self.redact_text(value)
            elif isinstance(value, dict):
                redacted[key] = self.redact_dict(value)
            elif isinstance(value, list):
                redacted[key] = [self.redact_text(item) if isinstance(item, str) else item for item in value]
            else:
                redacted[key] = value
        return redacted
    
    def redact_screenshot(self, image_data: bytes) -> bytes:
        """Placeholder for screenshot redaction (requires OCR)"""
        # In production, this would use OCR to detect and blur sensitive text
        # For now, we'll add metadata indicating redaction was applied
        logger.warning("Screenshot redaction requires OCR implementation")
        return image_data


class SecretManager(ABC):
    """Abstract base class for secret management backends"""
    
    @abstractmethod
    def get_secret(self, secret_name: str) -> Optional[str]:
        """Retrieve a secret by name"""
        pass
    
    @abstractmethod
    def set_secret(self, secret_name: str, secret_value: str) -> bool:
        """Store a secret"""
        pass
    
    @abstractmethod
    def delete_secret(self, secret_name: str) -> bool:
        """Delete a secret"""
        pass


class EnvironmentSecretManager(SecretManager):
    """Environment variable-based secret manager"""
    
    def get_secret(self, secret_name: str) -> Optional[str]:
        return os.getenv(secret_name)
    
    def set_secret(self, secret_name: str, secret_value: str) -> bool:
        os.environ[secret_name] = secret_value
        return True
    
    def delete_secret(self, secret_name: str) -> bool:
        if secret_name in os.environ:
            del os.environ[secret_name]
            return True
        return False


class AWSSecretsManager(SecretManager):
    """AWS Secrets Manager backend"""
    
    def __init__(self, region_name: str = None):
        if not AWS_AVAILABLE:
            raise ImportError("boto3 is required for AWS Secrets Manager")
        
        self.region_name = region_name or os.getenv("AWS_REGION", "us-east-1")
        self.client = boto3.client('secretsmanager', region_name=self.region_name)
    
    def get_secret(self, secret_name: str) -> Optional[str]:
        try:
            response = self.client.get_secret_value(SecretId=secret_name)
            if 'SecretString' in response:
                return response['SecretString']
            else:
                return base64.b64decode(response['SecretBinary']).decode('utf-8')
        except ClientError as e:
            logger.error(f"Error retrieving secret {secret_name}: {e}")
            return None
    
    def set_secret(self, secret_name: str, secret_value: str) -> bool:
        try:
            self.client.create_secret(Name=secret_name, SecretString=secret_value)
            return True
        except ClientError as e:
            if e.response['Error']['Code'] == 'ResourceExistsException':
                self.client.update_secret(SecretId=secret_name, SecretString=secret_value)
                return True
            logger.error(f"Error storing secret {secret_name}: {e}")
            return False
    
    def delete_secret(self, secret_name: str) -> bool:
        try:
            self.client.delete_secret(SecretId=secret_name, ForceDeleteWithoutRecovery=True)
            return True
        except ClientError as e:
            logger.error(f"Error deleting secret {secret_name}: {e}")
            return False


class HashiCorpVaultManager(SecretManager):
    """HashiCorp Vault backend"""
    
    def __init__(self, url: str = None, token: str = None):
        if not HASHICORP_AVAILABLE:
            raise ImportError("hvac is required for HashiCorp Vault")
        
        self.url = url or os.getenv("VAULT_ADDR", "http://localhost:8200")
        self.token = token or os.getenv("VAULT_TOKEN")
        self.client = hvac.Client(url=self.url, token=self.token)
    
    def get_secret(self, secret_name: str) -> Optional[str]:
        try:
            # Assuming KV v2 secrets engine
            response = self.client.secrets.kv.v2.read_secret_version(path=secret_name)
            return response['data']['data'].get('value')
        except Exception as e:
            logger.error(f"Error retrieving secret {secret_name} from Vault: {e}")
            return None
    
    def set_secret(self, secret_name: str, secret_value: str) -> bool:
        try:
            self.client.secrets.kv.v2.create_or_update_secret(
                path=secret_name,
                secret={'value': secret_value}
            )
            return True
        except Exception as e:
            logger.error(f"Error storing secret {secret_name} in Vault: {e}")
            return False
    
    def delete_secret(self, secret_name: str) -> bool:
        try:
            self.client.secrets.kv.v2.delete_metadata_and_all_versions(path=secret_name)
            return True
        except Exception as e:
            logger.error(f"Error deleting secret {secret_name} from Vault: {e}")
            return False


class TamperProofAuditLogger:
    """Tamper-proof audit logging with HMAC signatures"""
    
    def __init__(self, log_file: str = "veil_audit.log", 
                 signing_key: Optional[str] = None,
                 compliance_standards: List[ComplianceStandard] = None):
        self.log_file = Path(log_file)
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        
        # Use provided key or generate one
        self.signing_key = signing_key or os.getenv("AUDIT_SIGNING_KEY")
        if not self.signing_key:
            self.signing_key = base64.b64encode(os.urandom(32)).decode('utf-8')
            logger.warning("Generated new audit signing key. Store securely!")
        
        self.compliance_standards = compliance_standards or []
        self._lock = threading.Lock()
    
    def _sign_event(self, event_data: Dict[str, Any]) -> str:
        """Create HMAC signature for audit event"""
        event_str = json.dumps(event_data, sort_keys=True)
        signature = hmac.new(
            self.signing_key.encode('utf-8'),
            event_str.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        return signature
    
    def log_event(self, event: AuditEvent) -> bool:
        """Log an audit event with tamper-proof signature"""
        event_dict = event.to_dict()
        
        # Add compliance tags based on configured standards
        for standard in self.compliance_standards:
            if standard not in event_dict['compliance_tags']:
                event_dict['compliance_tags'].append(standard.value)
        
        # Create signature
        signature = self._sign_event(event_dict)
        event_dict['signature'] = signature
        
        # Write to log file
        with self._lock:
            try:
                with open(self.log_file, 'a') as f:
                    f.write(json.dumps(event_dict) + '\n')
                return True
            except Exception as e:
                logger.error(f"Failed to write audit log: {e}")
                return False
    
    def verify_log_integrity(self) -> Tuple[bool, List[str]]:
        """Verify the integrity of the audit log"""
        if not self.log_file.exists():
            return True, []
        
        tampered_events = []
        with open(self.log_file, 'r') as f:
            for line_num, line in enumerate(f, 1):
                try:
                    event_dict = json.loads(line.strip())
                    stored_signature = event_dict.pop('signature', None)
                    
                    if not stored_signature:
                        tampered_events.append(f"Line {line_num}: Missing signature")
                        continue
                    
                    # Recompute signature
                    computed_signature = self._sign_event(event_dict)
                    
                    if not hmac.compare_digest(stored_signature, computed_signature):
                        tampered_events.append(f"Line {line_num}: Signature mismatch")
                        
                except json.JSONDecodeError:
                    tampered_events.append(f"Line {line_num}: Invalid JSON")
        
        return len(tampered_events) == 0, tampered_events


class SecurityVault:
    """
    Main security vault class integrating all security features
    """
    
    def __init__(self, 
                 secret_backend: SecretBackend = SecretBackend.ENVIRONMENT,
                 audit_log_file: str = "veil_audit.log",
                 compliance_standards: List[ComplianceStandard] = None,
                 redaction_enabled: bool = True,
                 **backend_kwargs):
        
        # Initialize secret manager
        self.secret_manager = self._create_secret_manager(secret_backend, **backend_kwargs)
        
        # Initialize data redactor
        self.redactor = DataRedactor() if redaction_enabled else None
        
        # Initialize audit logger
        self.audit_logger = TamperProofAuditLogger(
            log_file=audit_log_file,
            compliance_standards=compliance_standards
        )
        
        # Cache for secrets
        self._secret_cache = {}
        self._cache_ttl = 300  # 5 minutes
        self._cache_timestamps = {}
        
        # Thread safety
        self._lock = threading.RLock()
        
        logger.info(f"SecurityVault initialized with {secret_backend.value} backend")
    
    def _create_secret_manager(self, backend: SecretBackend, **kwargs) -> SecretManager:
        """Create appropriate secret manager based on backend"""
        if backend == SecretBackend.ENVIRONMENT:
            return EnvironmentSecretManager()
        elif backend == SecretBackend.AWS_SECRETS_MANAGER:
            return AWSSecretsManager(**kwargs)
        elif backend == SecretBackend.HASHICORP_VAULT:
            return HashiCorpVaultManager(**kwargs)
        else:
            raise ValueError(f"Unsupported secret backend: {backend}")
    
    def get_secret(self, secret_name: str, use_cache: bool = True) -> Optional[str]:
        """Retrieve a secret with caching"""
        with self._lock:
            # Check cache
            if use_cache and secret_name in self._secret_cache:
                cache_time = self._cache_timestamps.get(secret_name, 0)
                if time.time() - cache_time < self._cache_ttl:
                    return self._secret_cache[secret_name]
            
            # Retrieve from backend
            secret_value = self.secret_manager.get_secret(secret_name)
            
            if secret_value:
                # Update cache
                self._secret_cache[secret_name] = secret_value
                self._cache_timestamps[secret_name] = time.time()
                
                # Log access (redacted)
                self.audit_logger.log_event(AuditEvent(
                    event_type="secret_access",
                    action="get_secret",
                    resource=secret_name,
                    details={"status": "success"}
                ))
            
            return secret_value
    
    def set_secret(self, secret_name: str, secret_value: str) -> bool:
        """Store a secret"""
        success = self.secret_manager.set_secret(secret_name, secret_value)
        
        self.audit_logger.log_event(AuditEvent(
            event_type="secret_management",
            action="set_secret",
            resource=secret_name,
            details={"status": "success" if success else "failed"}
        ))
        
        # Invalidate cache
        with self._lock:
            self._secret_cache.pop(secret_name, None)
            self._cache_timestamps.pop(secret_name, None)
        
        return success
    
    def redact_sensitive_data(self, data: Union[str, Dict, List]) -> Union[str, Dict, List]:
        """Redact sensitive data from various data types"""
        if not self.redactor:
            return data
        
        if isinstance(data, str):
            return self.redactor.redact_text(data)
        elif isinstance(data, dict):
            return self.redactor.redact_dict(data)
        elif isinstance(data, list):
            return [self.redact_sensitive_data(item) for item in data]
        else:
            return data
    
    def log_automation_action(self, 
                             action: str, 
                             page_url: str = None,
                             element_selector: str = None,
                             data: Dict[str, Any] = None,
                             session_id: str = None):
        """Log browser automation actions with redaction"""
        
        # Prepare details with redaction
        details = {
            "page_url": page_url,
            "element_selector": element_selector,
            "data": self.redact_sensitive_data(data) if data else None
        }
        
        # Remove None values
        details = {k: v for k, v in details.items() if v is not None}
        
        event = AuditEvent(
            event_type="browser_automation",
            action=action,
            resource=page_url or "unknown",
            details=details,
            session_id=session_id,
            compliance_tags=["data_processing"]
        )
        
        self.audit_logger.log_event(event)
    
    def create_compliance_report(self, start_date: datetime, end_date: datetime) -> Dict[str, Any]:
        """Generate compliance report for audit logs"""
        # This is a simplified version - production would parse the log file
        report = {
            "report_id": str(uuid.uuid4()),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "period": {
                "start": start_date.isoformat(),
                "end": end_date.isoformat()
            },
            "compliance_standards": [s.value for s in self.audit_logger.compliance_standards],
            "summary": {
                "total_events": 0,
                "events_by_type": {},
                "sensitive_data_redacted": True if self.redactor else False
            }
        }
        
        return report
    
    def verify_audit_integrity(self) -> Dict[str, Any]:
        """Verify audit log integrity and return report"""
        is_valid, issues = self.audit_logger.verify_log_integrity()
        
        return {
            "is_valid": is_valid,
            "issues_found": len(issues),
            "issues": issues[:10],  # Limit to first 10 issues
            "checked_at": datetime.now(timezone.utc).isoformat()
        }


# Decorator for automatic audit logging
def audit_action(action_name: str, resource: str = None):
    """Decorator to automatically audit function calls"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            # Try to find SecurityVault instance in args
            vault = None
            for arg in args:
                if isinstance(arg, SecurityVault):
                    vault = arg
                    break
            
            # Log start
            if vault:
                vault.log_automation_action(
                    action=f"{action_name}_start",
                    resource=resource,
                    data={"args": str(args), "kwargs": str(kwargs)}
                )
            
            try:
                result = func(*args, **kwargs)
                
                # Log success
                if vault:
                    vault.log_automation_action(
                        action=f"{action_name}_success",
                        resource=resource,
                        data={"result_type": type(result).__name__}
                    )
                
                return result
                
            except Exception as e:
                # Log failure
                if vault:
                    vault.log_automation_action(
                        action=f"{action_name}_failed",
                        resource=resource,
                        data={"error": str(e), "error_type": type(e).__name__}
                    )
                raise
        
        return wrapper
    return decorator


# Integration with existing veil modules
class SecurePageMixin:
    """Mixin to add security features to Page classes"""
    
    def __init__(self, *args, security_vault: SecurityVault = None, **kwargs):
        super().__init__(*args, **kwargs)
        self.security_vault = security_vault or SecurityVault()
    
    def secure_navigate(self, url: str, **kwargs):
        """Navigate with audit logging"""
        self.security_vault.log_automation_action(
            action="navigate",
            page_url=url,
            session_id=getattr(self, 'session_id', None)
        )
        return super().navigate(url, **kwargs)
    
    def secure_click(self, selector: str, **kwargs):
        """Click with audit logging"""
        self.security_vault.log_automation_action(
            action="click",
            element_selector=selector,
            session_id=getattr(self, 'session_id', None)
        )
        return super().click(selector, **kwargs)
    
    def secure_type(self, selector: str, text: str, **kwargs):
        """Type with sensitive data redaction"""
        # Redact sensitive data before logging
        redacted_text = self.security_vault.redact_sensitive_data(text)
        
        self.security_vault.log_automation_action(
            action="type",
            element_selector=selector,
            data={"text_length": len(text), "redacted": redacted_text != text},
            session_id=getattr(self, 'session_id', None)
        )
        
        return super().type(selector, text, **kwargs)


# Configuration helper
def create_security_vault_from_config(config: Dict[str, Any]) -> SecurityVault:
    """Create SecurityVault from configuration dictionary"""
    
    # Parse compliance standards
    compliance_standards = []
    for std in config.get("compliance_standards", []):
        try:
            compliance_standards.append(ComplianceStandard(std))
        except ValueError:
            logger.warning(f"Unknown compliance standard: {std}")
    
    # Parse secret backend
    backend_str = config.get("secret_backend", "environment")
    try:
        secret_backend = SecretBackend(backend_str)
    except ValueError:
        logger.warning(f"Unknown secret backend: {backend_str}, using environment")
        secret_backend = SecretBackend.ENVIRONMENT
    
    # Backend-specific configuration
    backend_kwargs = {}
    if secret_backend == SecretBackend.AWS_SECRETS_MANAGER:
        backend_kwargs["region_name"] = config.get("aws_region")
    elif secret_backend == SecretBackend.HASHICORP_VAULT:
        backend_kwargs["url"] = config.get("vault_url")
        backend_kwargs["token"] = config.get("vault_token")
    
    return SecurityVault(
        secret_backend=secret_backend,
        audit_log_file=config.get("audit_log_file", "veil_audit.log"),
        compliance_standards=compliance_standards,
        redaction_enabled=config.get("redaction_enabled", True),
        **backend_kwargs
    )


# Example usage in existing modules
def integrate_with_agent_service(agent_service, security_vault: SecurityVault):
    """Integrate security vault with existing AgentService"""
    
    # Store reference
    agent_service.security_vault = security_vault
    
    # Wrap methods with audit logging
    original_run = agent_service.run
    
    @wraps(original_run)
    def secure_run(*args, **kwargs):
        security_vault.log_automation_action(
            action="agent_run_start",
            data={"task": kwargs.get('task', args[0] if args else None)}
        )
        
        try:
            result = original_run(*args, **kwargs)
            security_vault.log_automation_action(
                action="agent_run_success",
                data={"result_length": len(str(result)) if result else 0}
            )
            return result
        except Exception as e:
            security_vault.log_automation_action(
                action="agent_run_failed",
                data={"error": str(e)}
            )
            raise
    
    agent_service.run = secure_run
    
    return agent_service


# Export main classes
__all__ = [
    'SecurityVault',
    'SecretBackend',
    'ComplianceStandard',
    'DataRedactor',
    'TamperProofAuditLogger',
    'AuditEvent',
    'audit_action',
    'SecurePageMixin',
    'create_security_vault_from_config',
    'integrate_with_agent_service',
]