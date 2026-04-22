"""
EventPort adapters.

Skor update + session event yayını:
    - RedisPubSubAdapter — PubSub channels (`tenant:{id}`, `session:{id}`) +
      resumable replay ZSET (`score_events:{session_id}`)
    - Phase 2.M: WebhookFanoutAdapter (HMAC-signed outbound webhooks, retry+DLQ)
    - Phase 2.N+: AuditLogAdapter (structured audit trail)
"""

from app.adapters.event.redis_pubsub import RedisPubSubAdapter

__all__ = ["RedisPubSubAdapter"]
