"""
veil/security/redactor.py

Enterprise Security & Compliance Module
Built-in security controls for sensitive data handling, audit logging, and compliance requirements.
"""

import re
import json
import hashlib
import logging
import base64
import os
from typing import Dict, List, Optional, Any, Union, Pattern
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from enum import Enum
import threading
from pathlib import Path

# Optional imports for external integrations
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
    import io
    HAS_PIL = True
except ImportError:
    HAS_PIL = False


class RedactionLevel(Enum):
    """Levels of redaction for different sensitivity contexts."""
    NONE = "none"
    LOW = "low"  # Basic patterns only
    MEDIUM = "medium"  # Standard compliance patterns
    HIGH = "high"  # Aggressive redaction for sensitive environments
    CUSTOM = "custom"


class AuditEventType(Enum):
    """Types of audit events for compliance logging."""
    PAGE_NAVIGATION = "page_navigation"
    ELEMENT_INTERACTION = "element_interaction"
    FORM_SUBMISSION = "form_submission"
    DATA_EXTRACTION = "data_extraction"
    SCREENSHOT_CAPTURE = "screenshot_capture"
    CREDENTIAL_ACCESS = "credential_access"
    SECRET_RETRIEVAL = "secret_retrieval"
    ERROR = "error"
    SESSION_START = "session_start"
    SESSION_END = "session_end"


@dataclass
class AuditEvent:
    """Immutable audit event record."""
    event_id: str
    timestamp: datetime
    event_type: AuditEventType
    session_id: str
    user_id: Optional[str] = None
    action: str = ""
    target: Optional[str] = None
    details: Dict[str, Any] = field(default_factory=dict)
    risk_score: int = 0  # 0-100, higher = more sensitive
    compliance_tags: List[str] = field(default_factory=list)
    redacted: bool = False
    checksum: Optional[str] = None

    def __post_init__(self):
        """Generate checksum for tamper detection."""
        if not self.checksum:
            self.checksum = self._generate_checksum()

    def _generate_checksum(self) -> str:
        """Generate SHA-256 checksum of event data for tamper detection."""
        data = {
            'event_id': self.event_id,
            'timestamp': self.timestamp.isoformat(),
            'event_type': self.event_type.value,
            'session_id': self.session_id,
            'action': self.action,
            'target': self.target,
            'details': self.details,
            'risk_score': self.risk_score
        }
        serialized = json.dumps(data, sort_keys=True).encode('utf-8')
        return hashlib.sha256(serialized).hexdigest()

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for serialization."""
        data = asdict(self)
        data['timestamp'] = self.timestamp.isoformat()
        data['event_type'] = self.event_type.value
        return data

    def verify_integrity(self) -> bool:
        """Verify event hasn't been tampered with."""
        expected = self._generate_checksum()
        return self.checksum == expected


class SensitiveDataPattern:
    """Pattern definition for sensitive data detection."""
    
    def __init__(self, 
                 name: str,
                 pattern: Union[str, Pattern],
                 replacement: str = "[REDACTED]",
                 risk_score: int = 50,
                 compliance_tags: List[str] = None):
        self.name = name
        self.pattern = re.compile(pattern) if isinstance(pattern, str) else pattern
        self.replacement = replacement
        self.risk_score = risk_score
        self.compliance_tags = compliance_tags or []
    
    def matches(self, text: str) -> bool:
        """Check if text contains this pattern."""
        return bool(self.pattern.search(text))
    
    def redact(self, text: str) -> str:
        """Redact all occurrences of this pattern in text."""
        return self.pattern.sub(self.replacement, text)


class SecurityRedactor:
    """
    Enterprise security redactor for sensitive data handling.
    Automatically detects and redacts PII, credentials, and sensitive data.
    """
    
    # Default patterns for common sensitive data
    DEFAULT_PATTERNS = [
        SensitiveDataPattern(
            name="email",
            pattern=r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',
            replacement="[EMAIL_REDACTED]",
            risk_score=70,
            compliance_tags=["GDPR", "PII"]
        ),
        SensitiveDataPattern(
            name="ssn",
            pattern=r'\b\d{3}-\d{2}-\d{4}\b',
            replacement="[SSN_REDACTED]",
            risk_score=100,
            compliance_tags=["GDPR", "PII", "SOC2"]
        ),
        SensitiveDataPattern(
            name="credit_card",
            pattern=r'\b(?:\d[ -]*?){13,16}\b',
            replacement="[CC_REDACTED]",
            risk_score=100,
            compliance_tags=["PCI-DSS", "SOC2"]
        ),
        SensitiveDataPattern(
            name="phone_number",
            pattern=r'\b(?:\+?1[-.]?)?\(?[0-9]{3}\)?[-.]?[0-9]{3}[-.]?[0-9]{4}\b',
            replacement="[PHONE_REDACTED]",
            risk_score=60,
            compliance_tags=["GDPR", "PII"]
        ),
        SensitiveDataPattern(
            name="ip_address",
            pattern=r'\b(?:\d{1,3}\.){3}\d{1,3}\b',
            replacement="[IP_REDACTED]",
            risk_score=40,
            compliance_tags=["GDPR"]
        ),
        SensitiveDataPattern(
            name="password_field",
            pattern=r'(?i)(password|passwd|pwd|secret|token|api_key|apikey)["\s:=]+[^\s"]{8,}',
            replacement="[CREDENTIAL_REDACTED]",
            risk_score=100,
            compliance_tags=["SOC2", "CREDENTIAL"]
        ),
        SensitiveDataPattern(
            name="aws_key",
            pattern=r'\b(?:AKIA|ASIA)[A-Z0-9]{16}\b',
            replacement="[AWS_KEY_REDACTED]",
            risk_score=100,
            compliance_tags=["AWS", "CREDENTIAL"]
        ),
        SensitiveDataPattern(
            name="private_key",
            pattern=r'-----BEGIN (?:RSA |EC |DSA )?PRIVATE KEY-----[\s\S]*?-----END (?:RSA |EC |DSA )?PRIVATE KEY-----',
            replacement="[PRIVATE_KEY_REDACTED]",
            risk_score=100,
            compliance_tags=["CREDENTIAL"]
        )
    ]
    
    def __init__(self, 
                 redaction_level: RedactionLevel = RedactionLevel.MEDIUM,
                 custom_patterns: List[SensitiveDataPattern] = None,
                 enable_image_redaction: bool = True,
                 log_redactions: bool = True):
        """
        Initialize the security redactor.
        
        Args:
            redaction_level: Level of redaction aggressiveness
            custom_patterns: Additional custom patterns to use
            enable_image_redaction: Whether to redact sensitive data in images
            log_redactions: Whether to log redaction events
        """
        self.redaction_level = redaction_level
        self.enable_image_redaction = enable_image_redaction and HAS_PIL
        self.log_redactions = log_redactions
        
        # Compile patterns based on redaction level
        self.patterns = self._compile_patterns(redaction_level, custom_patterns)
        
        # Cache for redacted content to avoid re-processing
        self._redaction_cache = {}
        self._cache_lock = threading.RLock()
        
        # Statistics
        self.stats = {
            'total_redactions': 0,
            'patterns_matched': {},
            'bytes_redacted': 0
        }
        
        self.logger = logging.getLogger(__name__)
    
    def _compile_patterns(self, 
                         level: RedactionLevel,
                         custom_patterns: List[SensitiveDataPattern] = None) -> List[SensitiveDataPattern]:
        """Compile patterns based on redaction level."""
        patterns = []
        
        # Add default patterns based on level
        if level in [RedactionLevel.LOW, RedactionLevel.MEDIUM, RedactionLevel.HIGH]:
            # All levels include basic patterns
            patterns.extend([p for p in self.DEFAULT_PATTERNS 
                           if p.risk_score <= 70 or level == RedactionLevel.HIGH])
        
        # Add custom patterns
        if custom_patterns:
            patterns.extend(custom_patterns)
        
        # For HIGH level, add more aggressive patterns
        if level == RedactionLevel.HIGH:
            patterns.append(SensitiveDataPattern(
                name="any_number_sequence",
                pattern=r'\b\d{4,}\b',
                replacement="[NUMBER_REDACTED]",
                risk_score=30,
                compliance_tags=["GDPR"]
            ))
        
        return patterns
    
    def redact_text(self, 
                   text: str, 
                   context: str = "",
                   session_id: str = None) -> str:
        """
        Redact sensitive data from text.
        
        Args:
            text: Text to redact
            context: Context for logging (e.g., "log_message", "form_input")
            session_id: Session ID for audit trail
            
        Returns:
            Redacted text
        """
        if not text or self.redaction_level == RedactionLevel.NONE:
            return text
        
        # Check cache first
        cache_key = hashlib.md5(text.encode('utf-8')).hexdigest()
        with self._cache_lock:
            if cache_key in self._redaction_cache:
                return self._redaction_cache[cache_key]
        
        redacted_text = text
        redaction_count = 0
        
        for pattern in self.patterns:
            if pattern.matches(redacted_text):
                redacted_text = pattern.redact(redacted_text)
                redaction_count += 1
                
                # Update statistics
                self.stats['patterns_matched'][pattern.name] = \
                    self.stats['patterns_matched'].get(pattern.name, 0) + 1
        
        # Update global statistics
        if redaction_count > 0:
            self.stats['total_redactions'] += redaction_count
            self.stats['bytes_redacted'] += len(text) - len(redacted_text)
            
            # Log redaction if enabled
            if self.log_redactions and session_id:
                self._log_redaction_event(text, redacted_text, context, session_id)
        
        # Cache the result
        with self._cache_lock:
            self._redaction_cache[cache_key] = redacted_text
        
        return redacted_text
    
    def redact_dict(self, 
                   data: Dict[str, Any], 
                   context: str = "",
                   session_id: str = None) -> Dict[str, Any]:
        """Recursively redact sensitive data from dictionary."""
        if not data:
            return data
        
        redacted = {}
        for key, value in data.items():
            if isinstance(value, str):
                redacted[key] = self.redact_text(value, f"{context}.{key}", session_id)
            elif isinstance(value, dict):
                redacted[key] = self.redact_dict(value, f"{context}.{key}", session_id)
            elif isinstance(value, list):
                redacted[key] = self.redact_list(value, f"{context}.{key}", session_id)
            else:
                redacted[key] = value
        
        return redacted
    
    def redact_list(self, 
                   data: List[Any], 
                   context: str = "",
                   session_id: str = None) -> List[Any]:
        """Recursively redact sensitive data from list."""
        redacted = []
        for i, item in enumerate(data):
            if isinstance(item, str):
                redacted.append(self.redact_text(item, f"{context}[{i}]", session_id))
            elif isinstance(item, dict):
                redacted.append(self.redact_dict(item, f"{context}[{i}]", session_id))
            elif isinstance(item, list):
                redacted.append(self.redact_list(item, f"{context}[{i}]", session_id))
            else:
                redacted.append(item)
        
        return redacted
    
    def redact_image(self, 
                    image_data: Union[bytes, str, 'Image.Image'],
                    regions: List[Dict[str, int]] = None) -> Optional[bytes]:
        """
        Redact sensitive regions in an image.
        
        Args:
            image_data: Image data (bytes, base64 string, or PIL Image)
            regions: List of regions to redact [{'x': int, 'y': int, 'width': int, 'height': int}]
            
        Returns:
            Redacted image as bytes, or None if PIL not available
        """
        if not self.enable_image_redaction:
            self.logger.warning("Image redaction disabled (PIL not available)")
            return None
        
        try:
            # Convert to PIL Image
            if isinstance(image_data, bytes):
                image = Image.open(io.BytesIO(image_data))
            elif isinstance(image_data, str):
                # Assume base64
                image_bytes = base64.b64decode(image_data)
                image = Image.open(io.BytesIO(image_bytes))
            else:
                image = image_data
            
            # If no specific regions, redact entire image for HIGH security
            if not regions and self.redaction_level == RedactionLevel.HIGH:
                # Blur the entire image
                from PIL import ImageFilter
                image = image.filter(ImageFilter.GaussianBlur(radius=10))
            elif regions:
                # Redact specific regions
                for region in regions:
                    x, y, w, h = region['x'], region['y'], region['width'], region['height']
                    # Create a black box over the region
                    from PIL import ImageDraw
                    draw = ImageDraw.Draw(image)
                    draw.rectangle([x, y, x + w, y + h], fill='black')
            
            # Convert back to bytes
            buffer = io.BytesIO()
            image.save(buffer, format='PNG')
            return buffer.getvalue()
            
        except Exception as e:
            self.logger.error(f"Image redaction failed: {e}")
            return None
    
    def _log_redaction_event(self, 
                           original: str, 
                           redacted: str, 
                           context: str,
                           session_id: str):
        """Log redaction event for audit trail."""
        # Don't log the actual sensitive data, just metadata
        self.logger.info(
            f"Redaction performed | Context: {context} | "
            f"Original length: {len(original)} | "
            f"Redacted length: {len(redacted)} | "
            f"Patterns matched: {len(self.patterns)}"
        )
    
    def get_statistics(self) -> Dict[str, Any]:
        """Get redaction statistics."""
        return self.stats.copy()
    
    def clear_cache(self):
        """Clear the redaction cache."""
        with self._cache_lock:
            self._redaction_cache.clear()


class AuditLogger:
    """
    Tamper-proof audit logger for compliance requirements.
    Provides detailed audit trails of all automation actions.
    """
    
    def __init__(self, 
                 log_file: str = "audit.log",
                 max_file_size: int = 100 * 1024 * 1024,  # 100MB
                 enable_encryption: bool = False,
                 encryption_key: Optional[bytes] = None):
        """
        Initialize the audit logger.
        
        Args:
            log_file: Path to audit log file
            max_file_size: Maximum log file size before rotation
            enable_encryption: Whether to encrypt log entries
            encryption_key: Encryption key (required if enable_encryption is True)
        """
        self.log_file = Path(log_file)
        self.max_file_size = max_file_size
        self.enable_encryption = enable_encryption
        self.encryption_key = encryption_key
        
        # Ensure log directory exists
        self.log_file.parent.mkdir(parents=True, exist_ok=True)
        
        # Thread lock for thread-safe writing
        self._lock = threading.RLock()
        
        # Session tracking
        self.current_session_id = None
        self.session_events = []
        
        self.logger = logging.getLogger(__name__)
        
        # Initialize encryption if enabled
        if enable_encryption and not encryption_key:
            raise ValueError("Encryption key required when encryption is enabled")
    
    def start_session(self, 
                     session_id: str, 
                     user_id: Optional[str] = None,
                     metadata: Dict[str, Any] = None) -> str:
        """
        Start a new audit session.
        
        Args:
            session_id: Unique session identifier
            user_id: User identifier (optional)
            metadata: Additional session metadata
            
        Returns:
            Session ID
        """
        self.current_session_id = session_id
        
        event = AuditEvent(
            event_id=self._generate_event_id(),
            timestamp=datetime.now(timezone.utc),
            event_type=AuditEventType.SESSION_START,
            session_id=session_id,
            user_id=user_id,
            action="session_start",
            details=metadata or {},
            compliance_tags=["SOC2", "GDPR"]
        )
        
        self._write_event(event)
        return session_id
    
    def end_session(self, session_id: Optional[str] = None):
        """End the current audit session."""
        sid = session_id or self.current_session_id
        if not sid:
            return
        
        event = AuditEvent(
            event_id=self._generate_event_id(),
            timestamp=datetime.now(timezone.utc),
            event_type=AuditEventType.SESSION_END,
            session_id=sid,
            action="session_end",
            details={"event_count": len(self.session_events)},
            compliance_tags=["SOC2", "GDPR"]
        )
        
        self._write_event(event)
        
        if sid == self.current_session_id:
            self.current_session_id = None
            self.session_events.clear()
    
    def log_event(self,
                 event_type: AuditEventType,
                 action: str,
                 target: Optional[str] = None,
                 details: Dict[str, Any] = None,
                 risk_score: int = 0,
                 compliance_tags: List[str] = None,
                 user_id: Optional[str] = None,
                 session_id: Optional[str] = None) -> AuditEvent:
        """
        Log an audit event.
        
        Args:
            event_type: Type of event
            action: Action performed
            target: Target of the action (e.g., URL, element selector)
            details: Additional details
            risk_score: Risk score (0-100)
            compliance_tags: Compliance tags for this event
            user_id: User identifier
            session_id: Session identifier
            
        Returns:
            Created AuditEvent
        """
        sid = session_id or self.current_session_id
        if not sid:
            sid = "unknown_session"
        
        event = AuditEvent(
            event_id=self._generate_event_id(),
            timestamp=datetime.now(timezone.utc),
            event_type=event_type,
            session_id=sid,
            user_id=user_id,
            action=action,
            target=target,
            details=details or {},
            risk_score=risk_score,
            compliance_tags=compliance_tags or []
        )
        
        self._write_event(event)
        self.session_events.append(event)
        
        return event
    
    def log_page_navigation(self,
                          url: str,
                          method: str = "GET",
                          status_code: Optional[int] = None,
                          session_id: Optional[str] = None) -> AuditEvent:
        """Log page navigation event."""
        return self.log_event(
            event_type=AuditEventType.PAGE_NAVIGATION,
            action="navigate",
            target=url,
            details={
                "method": method,
                "status_code": status_code,
                "timestamp": datetime.now(timezone.utc).isoformat()
            },
            risk_score=30,
            compliance_tags=["SOC2"],
            session_id=session_id
        )
    
    def log_element_interaction(self,
                              element_selector: str,
                              action: str,
                              value: Optional[str] = None,
                              session_id: Optional[str] = None) -> AuditEvent:
        """Log element interaction event."""
        # Redact value if it contains sensitive data
        redacted_value = None
        if value:
            redactor = SecurityRedactor()
            redacted_value = redactor.redact_text(value, "element_value", session_id)
        
        return self.log_event(
            event_type=AuditEventType.ELEMENT_INTERACTION,
            action=action,
            target=element_selector,
            details={
                "value": redacted_value,
                "original_length": len(value) if value else 0,
                "redacted": redacted_value != value if value else False
            },
            risk_score=50 if value else 20,
            compliance_tags=["SOC2", "GDPR"] if value else ["SOC2"],
            session_id=session_id
        )
    
    def log_credential_access(self,
                            credential_type: str,
                            source: str,
                            success: bool,
                            session_id: Optional[str] = None) -> AuditEvent:
        """Log credential access event."""
        return self.log_event(
            event_type=AuditEventType.CREDENTIAL_ACCESS,
            action="access_credential",
            target=credential_type,
            details={
                "source": source,
                "success": success,
                "timestamp": datetime.now(timezone.utc).isoformat()
            },
            risk_score=90,
            compliance_tags=["SOC2", "CREDENTIAL"],
            session_id=session_id
        )
    
    def _write_event(self, event: AuditEvent):
        """Write event to audit log with tamper protection."""
        with self._lock:
            try:
                # Convert event to JSON
                event_data = event.to_dict()
                
                # Add chain hash for tamper detection
                event_data['chain_hash'] = self._calculate_chain_hash(event)
                
                # Encrypt if enabled
                if self.enable_encryption:
                    event_data = self._encrypt_data(event_data)
                
                # Write to file
                with open(self.log_file, 'a', encoding='utf-8') as f:
                    f.write(json.dumps(event_data) + '\n')
                
                # Check file size and rotate if needed
                self._rotate_log_if_needed()
                
            except Exception as e:
                self.logger.error(f"Failed to write audit event: {e}")
    
    def _calculate_chain_hash(self, event: AuditEvent) -> str:
        """Calculate chain hash for tamper detection."""
        # Get previous event hash if exists
        previous_hash = "0" * 64  # Genesis hash
        
        if self.session_events:
            previous_event = self.session_events[-1]
            previous_hash = previous_event.checksum or previous_hash
        
        # Create chain hash
        chain_data = f"{previous_hash}:{event.checksum}"
        return hashlib.sha256(chain_data.encode('utf-8')).hexdigest()
    
    def _rotate_log_if_needed(self):
        """Rotate log file if it exceeds maximum size."""
        try:
            if self.log_file.stat().st_size > self.max_file_size:
                # Create backup with timestamp
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                backup_file = self.log_file.with_suffix(f".{timestamp}.log")
                self.log_file.rename(backup_file)
                
                # Keep only last 10 backups
                backups = sorted(self.log_file.parent.glob(f"{self.log_file.stem}.*.log"))
                for old_backup in backups[:-10]:
                    old_backup.unlink()
                    
        except Exception as e:
            self.logger.error(f"Failed to rotate audit log: {e}")
    
    def _encrypt_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Encrypt sensitive data in audit event."""
        if not self.encryption_key:
            return data
        
        # Simple XOR encryption for demonstration
        # In production, use proper encryption like AES-GCM
        import base64
        
        encrypted_data = data.copy()
        
        # Encrypt sensitive fields
        for field in ['details', 'target']:
            if field in encrypted_data and encrypted_data[field]:
                field_str = json.dumps(encrypted_data[field])
                encrypted = self._xor_encrypt(field_str, self.encryption_key)
                encrypted_data[field] = base64.b64encode(encrypted).decode('utf-8')
                encrypted_data[f'{field}_encrypted'] = True
        
        return encrypted_data
    
    def _xor_encrypt(self, data: str, key: bytes) -> bytes:
        """Simple XOR encryption (for demonstration only)."""
        data_bytes = data.encode('utf-8')
        key_len = len(key)
        encrypted = bytearray()
        
        for i, byte in enumerate(data_bytes):
            encrypted.append(byte ^ key[i % key_len])
        
        return bytes(encrypted)
    
    def _generate_event_id(self) -> str:
        """Generate unique event ID."""
        timestamp = datetime.now(timezone.utc).isoformat()
        random_bytes = os.urandom(8).hex()
        return f"evt_{timestamp}_{random_bytes}"
    
    def verify_log_integrity(self) -> Dict[str, Any]:
        """
        Verify the integrity of the audit log.
        
        Returns:
            Dictionary with verification results
        """
        results = {
            'total_events': 0,
            'valid_events': 0,
            'invalid_events': 0,
            'chain_integrity': True,
            'errors': []
        }
        
        try:
            with open(self.log_file, 'r', encoding='utf-8') as f:
                previous_hash = "0" * 64
                previous_checksum = None
                
                for line_num, line in enumerate(f, 1):
                    try:
                        event_data = json.loads(line.strip())
                        
                        # Verify event checksum
                        event = AuditEvent(
                            event_id=event_data['event_id'],
                            timestamp=datetime.fromisoformat(event_data['timestamp']),
                            event_type=AuditEventType(event_data['event_type']),
                            session_id=event_data['session_id'],
                            action=event_data['action'],
                            details=event_data.get('details', {}),
                            checksum=event_data.get('checksum')
                        )
                        
                        if event.verify_integrity():
                            results['valid_events'] += 1
                            
                            # Verify chain hash
                            expected_chain = self._calculate_chain_hash(event)
                            if event_data.get('chain_hash') != expected_chain:
                                results['chain_integrity'] = False
                                results['errors'].append(f"Chain broken at line {line_num}")
                            
                            previous_hash = event_data.get('chain_hash', previous_hash)
                            previous_checksum = event.checksum
                        else:
                            results['invalid_events'] += 1
                            results['errors'].append(f"Invalid checksum at line {line_num}")
                        
                        results['total_events'] += 1
                        
                    except (json.JSONDecodeError, KeyError, ValueError) as e:
                        results['errors'].append(f"Parse error at line {line_num}: {e}")
                        results['invalid_events'] += 1
                        results['total_events'] += 1
            
            return results
            
        except FileNotFoundError:
            return {'error': 'Audit log file not found'}
        except Exception as e:
            return {'error': f'Verification failed: {e}'}


class SecretManager:
    """
    Base class for secret manager integrations.
    Provides unified interface for retrieving secrets from various backends.
    """
    
    def __init__(self, backend: str = "env", **kwargs):
        """
        Initialize secret manager.
        
        Args:
            backend: Secret backend ("env", "vault", "aws", "file")
            **kwargs: Backend-specific configuration
        """
        self.backend = backend
        self.config = kwargs
        self._cache = {}
        self._cache_ttl = kwargs.get('cache_ttl', 300)  # 5 minutes default
        self._last_cache_update = {}
        
        self.logger = logging.getLogger(__name__)
    
    def get_secret(self, secret_name: str, version: str = None) -> Optional[str]:
        """
        Retrieve a secret by name.
        
        Args:
            secret_name: Name/identifier of the secret
            version: Version of the secret (if supported)
            
        Returns:
            Secret value or None if not found
        """
        # Check cache first
        cache_key = f"{secret_name}:{version or 'latest'}"
        if cache_key in self._cache:
            cached_time = self._last_cache_update.get(cache_key, 0)
            if datetime.now().timestamp() - cached_time < self._cache_ttl:
                return self._cache[cache_key]
        
        try:
            secret_value = None
            
            if self.backend == "env":
                secret_value = self._get_from_env(secret_name)
            elif self.backend == "vault":
                secret_value = self._get_from_vault(secret_name, version)
            elif self.backend == "aws":
                secret_value = self._get_from_aws(secret_name, version)
            elif self.backend == "file":
                secret_value = self._get_from_file(secret_name)
            else:
                raise ValueError(f"Unsupported backend: {self.backend}")
            
            # Cache the result
            if secret_value:
                self._cache[cache_key] = secret_value
                self._last_cache_update[cache_key] = datetime.now().timestamp()
            
            return secret_value
            
        except Exception as e:
            self.logger.error(f"Failed to retrieve secret '{secret_name}': {e}")
            return None
    
    def _get_from_env(self, secret_name: str) -> Optional[str]:
        """Get secret from environment variable."""
        # Try different naming conventions
        env_names = [
            secret_name.upper(),
            secret_name.lower(),
            secret_name.replace('-', '_').upper(),
            secret_name.replace('.', '_').upper()
        ]
        
        for env_name in env_names:
            value = os.getenv(env_name)
            if value:
                return value
        
        return None
    
    def _get_from_vault(self, secret_name: str, version: str = None) -> Optional[str]:
        """Get secret from HashiCorp Vault."""
        if not HAS_VAULT:
            raise ImportError("hvac package required for Vault integration")
        
        try:
            client = hvac.Client(
                url=self.config.get('vault_url', os.getenv('VAULT_ADDR')),
                token=self.config.get('vault_token', os.getenv('VAULT_TOKEN'))
            )
            
            # Parse secret path
            if '/' in secret_name:
                path, key = secret_name.rsplit('/', 1)
            else:
                path = secret_name
                key = None
            
            # Read secret
            if version:
                response = client.secrets.kv.v2.read_secret_version(
                    path=path,
                    version=version
                )
            else:
                response = client.secrets.kv.v2.read_secret_version(path=path)
            
            data = response['data']['data']
            
            if key:
                return data.get(key)
            else:
                # Return entire secret as JSON
                return json.dumps(data)
                
        except Exception as e:
            self.logger.error(f"Vault error: {e}")
            return None
    
    def _get_from_aws(self, secret_name: str, version: str = None) -> Optional[str]:
        """Get secret from AWS Secrets Manager."""
        if not HAS_AWS:
            raise ImportError("boto3 package required for AWS integration")
        
        try:
            client = boto3.client(
                'secretsmanager',
                region_name=self.config.get('region', os.getenv('AWS_REGION', 'us-east-1'))
            )
            
            kwargs = {'SecretId': secret_name}
            if version:
                kwargs['VersionId'] = version
            
            response = client.get_secret_value(**kwargs)
            
            if 'SecretString' in response:
                return response['SecretString']
            else:
                # Binary secret
                return base64.b64encode(response['SecretBinary']).decode('utf-8')
                
        except ClientError as e:
            error_code = e.response['Error']['Code']
            if error_code == 'ResourceNotFoundException':
                self.logger.warning(f"Secret not found: {secret_name}")
            else:
                self.logger.error(f"AWS Secrets Manager error: {e}")
            return None
    
    def _get_from_file(self, secret_name: str) -> Optional[str]:
        """Get secret from file."""
        secrets_file = self.config.get('secrets_file', 'secrets.json')
        
        try:
            with open(secrets_file, 'r') as f:
                secrets = json.load(f)
            
            # Support nested keys with dot notation
            keys = secret_name.split('.')
            value = secrets
            
            for key in keys:
                if isinstance(value, dict) and key in value:
                    value = value[key]
                else:
                    return None
            
            return str(value) if value is not None else None
            
        except (FileNotFoundError, json.JSONDecodeError) as e:
            self.logger.error(f"Failed to read secrets file: {e}")
            return None
    
    def clear_cache(self):
        """Clear the secrets cache."""
        self._cache.clear()
        self._last_cache_update.clear()


# Integration with existing veil modules
class SecurityIntegration:
    """
    Integration layer between security module and veil components.
    Provides hooks for automatic redaction and audit logging.
    """
    
    def __init__(self,
                 redactor: Optional[SecurityRedactor] = None,
                 audit_logger: Optional[AuditLogger] = None,
                 secret_manager: Optional[SecretManager] = None):
        """
        Initialize security integration.
        
        Args:
            redactor: Security redactor instance
            audit_logger: Audit logger instance
            secret_manager: Secret manager instance
        """
        self.redactor = redactor or SecurityRedactor()
        self.audit_logger = audit_logger or AuditLogger()
        self.secret_manager = secret_manager or SecretManager()
        
        # Monkey-patch existing modules for automatic security
        self._patch_existing_modules()
    
    def _patch_existing_modules(self):
        """Patch existing veil modules with security hooks."""
        try:
            # Import existing modules
            from veil.actor import page, element
            from veil.agent import service
            
            # Store original methods
            original_navigate = page.PageActor.navigate if hasattr(page, 'PageActor') else None
            original_click = element.ElementActor.click if hasattr(element, 'ElementActor') else None
            original_type = element.ElementActor.type if hasattr(element, 'ElementActor') else None
            
            # Create patched versions with security hooks
            if original_navigate:
                def secure_navigate(self, url, *args, **kwargs):
                    # Log navigation
                    if hasattr(self, 'audit_logger'):
                        self.audit_logger.log_page_navigation(url)
                    
                    # Call original method
                    return original_navigate(self, url, *args, **kwargs)
                
                page.PageActor.navigate = secure_navigate
            
            # Similar patches for other methods...
            
        except ImportError as e:
            logging.warning(f"Could not patch existing modules: {e}")
    
    def redact_browser_logs(self, logs: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Redact sensitive data from browser logs."""
        redacted_logs = []
        
        for log in logs:
            redacted_log = log.copy()
            
            # Redact message field if present
            if 'message' in redacted_log:
                redacted_log['message'] = self.redactor.redact_text(
                    redacted_log['message'],
                    "browser_log"
                )
            
            # Redact any other string fields
            for key, value in redacted_log.items():
                if isinstance(value, str) and key != 'message':
                    redacted_log[key] = self.redactor.redact_text(
                        value,
                        f"browser_log.{key}"
                    )
            
            redacted_logs.append(redacted_log)
        
        return redacted_logs
    
    def secure_screenshot(self, 
                         screenshot_data: bytes,
                         sensitive_regions: List[Dict[str, int]] = None) -> bytes:
        """
        Take a secure screenshot with sensitive data redaction.
        
        Args:
            screenshot_data: Raw screenshot data
            sensitive_regions: Regions to redact
            
        Returns:
            Redacted screenshot
        """
        # Log screenshot event
        self.audit_logger.log_event(
            event_type=AuditEventType.SCREENSHOT_CAPTURE,
            action="capture_screenshot",
            details={
                "size": len(screenshot_data),
                "regions_redacted": len(sensitive_regions) if sensitive_regions else 0
            },
            risk_score=40,
            compliance_tags=["GDPR", "SOC2"]
        )
        
        # Redact sensitive regions
        return self.redactor.redact_image(screenshot_data, sensitive_regions)


# Factory functions for easy instantiation
def create_redactor(level: str = "medium", **kwargs) -> SecurityRedactor:
    """
    Create a security redactor with specified level.
    
    Args:
        level: Redaction level ("none", "low", "medium", "high")
        **kwargs: Additional arguments for SecurityRedactor
        
    Returns:
        Configured SecurityRedactor instance
    """
    level_map = {
        "none": RedactionLevel.NONE,
        "low": RedactionLevel.LOW,
        "medium": RedactionLevel.MEDIUM,
        "high": RedactionLevel.HIGH
    }
    
    return SecurityRedactor(
        redaction_level=level_map.get(level.lower(), RedactionLevel.MEDIUM),
        **kwargs
    )


def create_audit_logger(log_dir: str = "./audit_logs", **kwargs) -> AuditLogger:
    """
    Create an audit logger with default configuration.
    
    Args:
        log_dir: Directory for audit logs
        **kwargs: Additional arguments for AuditLogger
        
    Returns:
        Configured AuditLogger instance
    """
    log_file = os.path.join(log_dir, f"audit_{datetime.now().strftime('%Y%m%d')}.log")
    return AuditLogger(log_file=log_file, **kwargs)


def create_secret_manager(backend: str = "env", **kwargs) -> SecretManager:
    """
    Create a secret manager with specified backend.
    
    Args:
        backend: Secret backend ("env", "vault", "aws", "file")
        **kwargs: Backend-specific configuration
        
    Returns:
        Configured SecretManager instance
    """
    return SecretManager(backend=backend, **kwargs)


# Example usage and testing
if __name__ == "__main__":
    # Configure logging
    logging.basicConfig(level=logging.INFO)
    
    # Create security components
    redactor = create_redactor("high")
    audit_logger = create_audit_logger()
    secret_manager = create_secret_manager("env")
    
    # Test redaction
    test_text = "User email: john.doe@example.com, SSN: 123-45-6789, Password: secret123"
    redacted = redactor.redact_text(test_text)
    print(f"Original: {test_text}")
    print(f"Redacted: {redacted}")
    
    # Test audit logging
    session_id = audit_logger.start_session("test_session_123")
    audit_logger.log_page_navigation("https://example.com")
    audit_logger.end_session()
    
    # Verify log integrity
    integrity = audit_logger.verify_log_integrity()
    print(f"Audit log integrity: {integrity}")
    
    # Test secret retrieval
    secret = secret_manager.get_secret("API_KEY")
    print(f"Secret retrieved: {'Yes' if secret else 'No'}")