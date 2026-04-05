# Webhook Integration Guide

> This document is intended for companies and integration partners
> who want to send lead data to the AI Lead Qualifier webhook.

---

## 1. How It Works

You will receive a unique webhook URL. You send lead information to this URL as a JSON POST request.
The system automatically evaluates the lead and returns the result.

---

## 2. Webhook URL

```
POST https://{domain}/webhook/lead/{company_token}
```

- `{domain}` — The server address provided to you
- `{company_token}` — A unique token generated for your company

**Example:**
```
POST https://api.lisent.ai/webhook/lead/whlead_9a6dda3ce92503c46f3b82dfc16a1e94c31a833cdded7f4fcaf31c013592554b
```

> Your token will be shared with you separately.

---

## 3. Request Specifications

| Property     | Value              |
|--------------|--------------------|
| Method       | `POST`             |
| Content-Type | `application/json` |
| Success Code | `202 Accepted`     |
| Encoding     | UTF-8              |

No additional authentication header is required. The token is embedded in the URL.

---

## 4. Data to Send (JSON)

### Required Fields

| Field   | Type   | Constraint      | Description                          |
|---------|--------|-----------------|--------------------------------------|
| `name`  | string | 1-200 characters | Full name of the lead               |
| `phone` | string | 5-30 characters  | Phone number (e.g. "05001234567")   |

### Recommended Fields

The more information you provide, the more accurate the lead evaluation will be.

| Field                | Type   | Accepted Values                                                          |
|----------------------|--------|--------------------------------------------------------------------------|
| `email`              | string | Email address                                                            |
| `city`               | string | City (free text)                                                         |
| `source`             | string | `instagram`, `facebook`, `tiktok`, `linkedin`, `website`, `whatsapp`, `other` |
| `project_type`       | string | `residential`, `commercial`, `industrial`, `renovation`, `land`, `other` |
| `budget_range`       | string | `under_500k`, `500k_1m`, `1m_3m`, `3m_10m`, `over_10m`, `unknown`       |
| `budget_amount`      | number | Budget amount (TRY, numeric — e.g. `1500000`)                           |
| `decision_authority` | string | `sole`, `joint`, `influencer`, `unknown`                                 |
| `timeline_urgency`   | string | `immediate`, `short`, `medium`, `long`, `unknown`                        |
| `notes`              | string | Additional notes or comments (free text)                                 |
| `lead_id`            | string | A unique ID from your system (auto-generated if not provided)            |

### Additional / Custom Fields

You can send any extra fields beyond those listed above.
All additional fields are automatically stored.

```json
{
  "name": "Ali Veli",
  "phone": "05001234567",
  "campaign_id": "KMP-2024-001",
  "form_name": "Instagram Housing Form"
}
```

---

## 5. Example Payloads

### Minimum (required fields only)

```json
{
  "name": "Ayse Yilmaz",
  "phone": "05321234567"
}
```

### Typical Ad Form

```json
{
  "name": "Fatma Demir",
  "phone": "05441234567",
  "email": "fatma@gmail.com",
  "source": "instagram",
  "project_type": "residential",
  "budget_range": "1m_3m",
  "notes": "Looking to build a villa, already owns the land"
}
```

### Detailed Lead

```json
{
  "name": "Mehmet Kaya",
  "phone": "05551234567",
  "email": "mehmet@kayainsaat.com",
  "city": "Istanbul",
  "source": "linkedin",
  "project_type": "commercial",
  "budget_range": "over_10m",
  "budget_amount": 15000000,
  "decision_authority": "sole",
  "timeline_urgency": "immediate",
  "notes": "5-story commercial building project in Kadikoy, wants to start immediately"
}
```

---

## 6. Response Format

On successful submissions, the server returns HTTP `202 Accepted`:

```json
{
  "status": "accepted",
  "score": 85,
  "lead_id": "f47ac10b-58cc-4372-a567-0e02b2c3d479"
}
```

### Error Responses

| HTTP Code | Meaning                                                |
|-----------|--------------------------------------------------------|
| 400       | Invalid JSON format or missing required field          |
| 404       | Invalid or unregistered company_token                  |
| 422       | Validation error (e.g. phone shorter than 5 characters)|
| 500       | Server error — please retry                            |

---

## 7. Integration Examples

### cURL

```bash
curl -X POST "https://api.lisent.ai/webhook/lead/YOUR_COMPANY_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "name": "Ali Veli",
    "phone": "05001234567",
    "email": "ali@example.com",
    "source": "facebook",
    "project_type": "residential",
    "budget_range": "1m_3m"
  }'
```

### Python

```python
import requests

WEBHOOK_URL = "https://api.lisent.ai/webhook/lead/YOUR_COMPANY_TOKEN"

def send_lead(lead_data: dict):
    response = requests.post(WEBHOOK_URL, json=lead_data, timeout=10)
    response.raise_for_status()
    return response.json()

# Usage
result = send_lead({
    "name": "Ali Veli",
    "phone": "05001234567",
    "email": "ali@example.com",
    "source": "instagram",
    "project_type": "commercial",
    "budget_range": "3m_10m",
})
print(result)
```

### JavaScript / Node.js

```javascript
const WEBHOOK_URL = "https://api.lisent.ai/webhook/lead/YOUR_COMPANY_TOKEN";

async function sendLead(leadData) {
  const response = await fetch(WEBHOOK_URL, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(leadData),
  });

  if (!response.ok) throw new Error(`HTTP ${response.status}`);
  return response.json();
}

// Usage
sendLead({
  name: "Ali Veli",
  phone: "05001234567",
  email: "ali@example.com",
  source: "facebook",
  project_type: "residential",
  budget_range: "500k_1m",
}).then(console.log);
```

### PHP

```php
<?php
$url = "https://api.lisent.ai/webhook/lead/YOUR_COMPANY_TOKEN";

$data = [
    "name"         => "Ali Veli",
    "phone"        => "05001234567",
    "email"        => "ali@example.com",
    "source"       => "website",
    "project_type" => "renovation",
    "budget_range" => "500k_1m"
];

$ch = curl_init($url);
curl_setopt($ch, CURLOPT_POST, true);
curl_setopt($ch, CURLOPT_POSTFIELDS, json_encode($data));
curl_setopt($ch, CURLOPT_HTTPHEADER, ["Content-Type: application/json"]);
curl_setopt($ch, CURLOPT_RETURNTRANSFER, true);
curl_setopt($ch, CURLOPT_TIMEOUT, 10);

$response = curl_exec($ch);
$code = curl_getinfo($ch, CURLINFO_HTTP_CODE);
curl_close($ch);

echo ($code === 202) ? $response : "Error: HTTP $code";
```

### C# / .NET

```csharp
using System.Net.Http;
using System.Text;
using System.Text.Json;

var client = new HttpClient();
var url = "https://api.lisent.ai/webhook/lead/YOUR_COMPANY_TOKEN";

var data = new {
    name = "Ali Veli",
    phone = "05001234567",
    email = "ali@example.com",
    source = "website",
    project_type = "commercial",
    budget_range = "3m_10m"
};

var content = new StringContent(
    JsonSerializer.Serialize(data), Encoding.UTF8, "application/json"
);

var response = await client.PostAsync(url, content);
Console.WriteLine(await response.Content.ReadAsStringAsync());
```

---

## 8. Ad Platform Integrations

### Facebook / Instagram Lead Ads

Facebook Lead Ads data comes in a different format. Transform it before forwarding:

```python
def facebook_to_webhook(fb_lead: dict) -> dict:
    fields = {f["name"]: f["values"][0] for f in fb_lead.get("field_data", [])}
    return {
        "name": fields.get("full_name", ""),
        "phone": fields.get("phone_number", ""),
        "email": fields.get("email", ""),
        "source": "facebook",
        "lead_id": fb_lead.get("id"),
        "notes": fields.get("description", ""),
        "city": fields.get("city", ""),
    }
```

### Google Ads Lead Form Extensions

```python
def google_to_webhook(g_lead: dict) -> dict:
    cols = {c["column_id"]: c["string_value"] for c in g_lead.get("user_column_data", [])}
    return {
        "name": cols.get("FULL_NAME", ""),
        "phone": cols.get("PHONE_NUMBER", ""),
        "email": cols.get("EMAIL", ""),
        "source": "website",
        "city": cols.get("CITY", ""),
    }
```

### Zapier / Make (Integromat) / n8n

1. **Trigger:** Ad platform or form tool (Typeform, JotForm, Google Forms, etc.)
2. **Action:** HTTP Request module
   - URL: `https://api.lisent.ai/webhook/lead/YOUR_COMPANY_TOKEN`
   - Method: POST
   - Content-Type: application/json
   - Body: Map trigger fields to `name`, `phone`, `email`, etc.

---

## 9. Receiving Qualified Leads (Callback / Fallback URL)

You can provide a **callback URL** (fallback URL) so that once a lead has been scored and qualified,
our system automatically sends the result back to your endpoint. This way you receive the enriched,
scored lead data in your own system in real time.

### How It Works

1. You set up a webhook endpoint on your server that accepts `POST` requests with JSON body.
2. You provide that URL to us (during onboarding or via your CRM panel).
3. When a lead is qualified, our system sends a `POST` request to your URL with the scored lead data.

### What You Will Receive

Your endpoint will receive a JSON payload like this:

```json
{
  "lead": {
    "id": "f47ac10b-58cc-4372-a567-0e02b2c3d479",
    "name": "Mehmet Kaya",
    "phone": "05551234567",
    "email": "mehmet@kayainsaat.com",
    "city": "Istanbul",
    "source": "linkedin",
    "project_type": "commercial",
    "budget_range": "over_10m",
    "budget_amount": 15000000,
    "decision_authority": "sole",
    "timeline_urgency": "immediate",
    "notes": "5-story commercial building in Kadikoy"
  },
  "score": 95,
  "score_breakdown": {
    "budget": 30,
    "timeline": 25,
    "project_type": 20,
    "authority": 15,
    "data_quality": 10
  },
  "reasoning_report": {
    "summary": "High-value commercial lead with immediate timeline...",
    "recommendation": "Priority follow-up recommended"
  },
  "bant": null,
  "session_id": null,
  "path": "fast"
}
```

| Field              | Description                                                        |
|--------------------|--------------------------------------------------------------------|
| `lead`             | The original lead data you sent, enriched with any extracted info  |
| `score`            | Final qualification score (0-100)                                  |
| `score_breakdown`  | Score per evaluation dimension                                     |
| `reasoning_report` | AI-generated analysis and recommendation (may be `null`)           |
| `bant`             | BANT extraction result if lead went through chat (may be `null`)   |
| `session_id`       | Chat session ID if lead went through chat (may be `null`)          |
| `path`             | `"fast"` (auto-qualified) or `"chat"` (qualified via conversation) |

### Your Endpoint Requirements

| Requirement    | Details                                                          |
|----------------|------------------------------------------------------------------|
| Method         | Must accept `POST`                                               |
| Content-Type   | `application/json`                                               |
| Response       | Return `2xx` status code on success                              |
| Timeout        | Respond within 10 seconds                                        |
| Auth (optional)| If needed, we can send a `Bearer` token in the `Authorization` header |

### Retry Behavior

If your endpoint is unreachable or returns a non-2xx status:
- We retry up to **3 times** with exponential backoff (2-10 seconds between attempts).
- If all retries fail, the payload is queued and retried automatically in the background.
- No data is lost — failed deliveries are eventually retried.

### Example: Receiving Endpoint (Python / Flask)

```python
from flask import Flask, request, jsonify

app = Flask(__name__)

@app.route("/webhook/qualified-lead", methods=["POST"])
def receive_qualified_lead():
    data = request.json

    lead = data["lead"]
    score = data["score"]
    path = data["path"]

    # Process the qualified lead in your system
    print(f"Received lead: {lead['name']} — Score: {score} — Path: {path}")

    # Save to your database, notify sales team, etc.
    # ...

    return jsonify({"status": "ok"}), 200
```

### Example: Receiving Endpoint (Node.js / Express)

```javascript
const express = require("express");
const app = express();
app.use(express.json());

app.post("/webhook/qualified-lead", (req, res) => {
  const { lead, score, path } = req.body;

  console.log(`Received lead: ${lead.name} — Score: ${score} — Path: ${path}`);

  // Save to your database, notify sales team, etc.
  // ...

  res.status(200).json({ status: "ok" });
});

app.listen(3000);
```

### Example: Receiving Endpoint (PHP)

```php
<?php
$input = json_decode(file_get_contents("php://input"), true);

$lead = $input["lead"];
$score = $input["score"];
$path  = $input["path"];

// Process the qualified lead
error_log("Received lead: {$lead['name']} — Score: $score — Path: $path");

// Save to your database, notify sales team, etc.
// ...

http_response_code(200);
echo json_encode(["status" => "ok"]);
```

---

## 10. Important Notes

| Topic                | Details                                                                                 |
|----------------------|-----------------------------------------------------------------------------------------|
| **Deduplication**    | A second submission with the same `lead_id` will be rejected as `duplicate`             |
| **Flexible Schema**  | Undefined extra fields are accepted and stored — nothing is lost                        |
| **Timeout**          | We recommend setting a 10-second timeout on your requests                               |
| **Retry**            | On 5xx errors, wait 2-3 seconds and retry. Use `lead_id` to prevent duplicates          |
| **HTTPS**            | Always use HTTPS in production                                                          |
| **Bulk Sending**     | If sending many leads at once, wait at least 100ms between requests                     |

---

## 10. Testing

To verify your integration:

```bash
# Simple test
curl -X POST "https://api.lisent.ai/webhook/lead/YOUR_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name": "Test Lead", "phone": "05001112233"}'

# On success, returns HTTP 202 with a JSON response
```

---

## 11. FAQ

**Q: Is it enough to send just name and phone?**
A: Yes. However, the more information you provide, the more accurate the evaluation will be.

**Q: Can I include my own custom fields?**
A: Yes, you can send any additional fields. They will all be stored.

**Q: What happens if I send the same person twice?**
A: If you include a `lead_id`, the second submission will be rejected. If you don't, it will be processed as a new lead.

**Q: Will my token change?**
A: No, your token is permanent. Contact us if you need a new one.

**Q: What data formats do you accept?**
A: JSON only. XML, form-data, etc. are not supported.

---

## Support

If you encounter any issues during integration, please contact us.
