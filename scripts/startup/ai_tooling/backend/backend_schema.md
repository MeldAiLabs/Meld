# LLM Backend API Schema

This document describes the API endpoints for the LLM backend service.

## Authentication

All endpoints require an `Authorization` header with a Bearer token for authentication.

*   **Example Header:** `Authorization: Bearer fake-token-user-1`

---

## Endpoint: `/process-message`

**Method:** `POST`

**Description:** Processes a user message or the results of a tool execution within a specific conversation thread. Returns the AI's next response, which could be a text message, a request for tool execution, or an error.

**Request Body Schema (JSON)**

```json
{
  "type": "object",
  "properties": {
    "thread_id": {
      "type": "string",
      "description": "A unique identifier (20 characters) for the conversation thread. Reuse this ID for subsequent messages/tool results in the same conversation.",
      "pattern": "^.{20}$"
    },
    "message": {
      "type": "string",
      "description": "The text content of the user's message. Required if 'tool_results' is not provided."
    },
    "tool_results": {
      "type": "array",
      "description": "A list of results from previously requested tool calls. Required if 'message' is not provided.",
      "items": {
        "type": "object",
        "properties": {
          "tool_call_id": {
            "type": "string",
            "description": "The unique ID of the tool call this result corresponds to."
          },
          "content": {
            "type": "string",
            "description": "The output/result from the tool execution (can be JSON string, text, etc.)."
          }
        },
        "required": ["tool_call_id", "content"]
      }
    },
    "checkpoint_id": {
      "type": "string",
      "description": "Optional. The specific checkpoint ID (UUID format) to resume the conversation from. If omitted, resumes from the latest state."
    },
    "model_name": {
      "type": "string",
      "description": "Optional. The name of the model to use for processing this message. If omitted, the default model is used."
    }
  },
  "required": ["thread_id"],
  "anyOf": [
    { "required": ["message"] },
    { "required": ["tool_results"] }
  ]
}
```

**Example Request Body (User Message):**

```json
{
  "thread_id": "thread_s9fG3kR8nLpW7xZqVbA2",
  "message": "Hello, who are you?",
  "checkpoint_id": "f8e4c9b0-1d2a-4b7c-8a9e-0f1d2c3b4a5e", // Optional
  "model_name": "gpt-4.1" 
}
```

**Example Request Body (Tool Result):**

```json
{
  "thread_id": "thread_s9fG3kR8nLpW7xZqVbA2",
  "tool_results": [
    {
      "tool_call_id": "call_xyz789",
      "content": "{\"status\": \"success\", \"message\": \"Cube created successfully.\"}"
    }
  ],
  "model_name": "gpt-4.1" 
}
```

**Response Body Schema (Status Code: 200 OK)**

The actual AI response (`type`, `data`) is nested within a top-level `response` object, which also includes the `status_code` suggested by the backend and the `new_checkpoint_id` referencing the state *after* this interaction.

```json
{
  "type": "object",
  "properties": {
    "response": {
      "type": "object",
      "properties": {
        "status_code": {
          "type": "integer",
          "description": "The suggested HTTP status code for this response (e.g., 200 for success, 400/500 for errors handled by the agent)."
        },
        "type": {
          "type": "string",
          "enum": ["ai_message", "tool_calls", "error"],
          "description": "Indicates the type of the agent's response ('error' type here means an error occurred during agent processing, not necessarily a 5xx HTTP error)."
        },
        "data": {
          "oneOf": [
            { "$ref": "#/components/schemas/AIData" },
            { "$ref": "#/components/schemas/ToolCallsData" },
            { "$ref": "#/components/schemas/ErrorData" }
          ],
          "description": "The actual content of the response, structure depends on the 'type'."
        },
        "new_checkpoint_id": {
            "type": ["string", "null"],
            "description": "The checkpoint ID representing the conversation state *after* this interaction. Use this in subsequent requests to continue from this point. Null if an error prevented saving state."
        }
      },
      "required": ["status_code", "type", "data", "new_checkpoint_id"]
    }
  },
  "required": ["response"]
}
```

**Component Schemas for `data`:**

```json
// In a real OpenAPI spec, these would be under "components/schemas"
{
  "AIData": {
    "type": "string",
    "description": "The assistant's text reply (used when response.type is 'ai_message')."
  },
  "ToolCallsData": {
    "type": "array",
    "description": "A list of tool calls requested by the assistant (used when response.type is 'tool_calls').",
    "items": {
      "type": "object",
      "properties": {
        "id": { "type": "string", "description": "A unique ID for this specific tool call." },
        "name": { "type": "string", "description": "The name of the function/tool to call." },
        "args": { "type": "object", "description": "The arguments for the function." },
        "type": { "type": "string", "enum": ["tool_code", "other_tool_type"], "description": "The type of the tool." } // Example enum
      },
      "required": ["id", "name", "args", "type"]
    }
  },
  "ErrorData": {
      "type": "string",
      "description": "Details about the error encountered during agent processing (used when response.type is 'error')."
  }
}
```

**Example Success Response (AI Message):**

```json
{
  "response": {
    "status_code": 200,
    "type": "ai_message",
    "data": "Okay, I have created the cube.",
    "new_checkpoint_id": "a1b2c3d4-e5f6-7890-1234-567890abcdef"
  }
}
```

**Example Success Response (Tool Calls):**

```json
{
  "response": {
    "status_code": 200,
    "type": "tool_calls",
    "data": [
      {
        "id": "call_abc123",
        "name": "set_object_color",
        "args": { "object_name": "Cube", "color": [1.0, 0.0, 0.0] },
        "type": "tool_code" // Assuming this type exists
      }
    ],
    "new_checkpoint_id": "b2c3d4e5-f6a7-8901-2345-67890abcdef1"
  }
}
```

**Example Success Response (Agent Error):**

```json
{
  "response": {
    "status_code": 500,
    "type": "error",
    "data": "Agent failed after retries: [Details of the LLM error]",
    "new_checkpoint_id": "c3d4e5f6-a7b8-9012-3456-7890abcdef12" // Might be the previous checkpoint ID if saving failed
  }
}
```

---

## Endpoint: `/threads`

**Method:** `GET`

**Description:** Retrieves a list of all conversation threads associated with the authenticated user, ordered by the most recently accessed.

**Response Body Schema (Status Code: 200 OK)**

```json
{
  "type": "object",
  "properties": {
    "threads": {
      "type": "array",
      "description": "A list of thread objects.",
      "items": {
        "type": "object",
        "properties": {
          "thread_id": {
            "type": "string",
            "description": "The unique identifier for the conversation thread."
          },
          "created_at": {
            "type": "string",
            "format": "date-time",
            "description": "ISO 8601 timestamp of when the thread was first created."
          },
          "last_accessed_at": {
            "type": "string",
            "format": "date-time",
            "description": "ISO 8601 timestamp of when the thread was last accessed (message sent or history retrieved)."
          }
        },
        "required": ["thread_id", "created_at", "last_accessed_at"]
      }
    }
  },
  "required": ["threads"]
}
```

**Example Success Response:**

```json
{
  "threads": [
    {
      "thread_id": "thread_s9fG3kR8nLpW7xZqVbA2",
      "created_at": "2023-10-27T10:00:00Z",
      "last_accessed_at": "2023-10-27T10:15:30Z"
    },
    {
      "thread_id": "thread_xYzA1bC2dE3fG4hI5jK6",
      "created_at": "2023-10-26T09:00:00Z",
      "last_accessed_at": "2023-10-26T09:05:00Z"
    }
  ]
}
```

---

## Endpoint: `/threads/{thread_id}/history`

**Method:** `GET`

**Description:** Retrieves the conversation history for a specific thread owned by the authenticated user. Each message includes a checkpoint_id, allowing clients to implement "time travel" functionality by resuming conversations from specific points in history.

**Path Parameters:**

*   `thread_id` (string, required): The unique identifier (20 characters) of the conversation thread.

**Response Body Schema (Status Code: 200 OK)**

```json
{
  "type": "object",
  "properties": {
    "history": {
      "type": "array",
      "description": "The ordered list of messages in the conversation.",
      "items": {
        "type": "object",
        "properties": {
          "type": {
            "type": "string",
            "enum": ["human", "ai", "tool", "system", "unknown"],
            "description": "The type or role of the message sender."
          },
          "content": {
            "type": ["string", "null"],
            "description": "The text content of the message (can be null for AI messages that only contain tool calls)."
          },
          "checkpoint_id": {
            "type": "string",
            "description": "The unique ID of the checkpoint representing the conversation state after this message. Can be used with the /process-message endpoint to resume from this point."
          },
          "tool_calls": {
            "type": ["array", "null"],
            "description": "Present for 'ai' type messages if the AI requested tool execution. Null otherwise.",
            "items": {
              "type": "object",
              "properties": {
                "id": { "type": "string", "description": "The ID assigned to the tool call." },
                "name": { "type": "string", "description": "The name of the tool/function called." },
                "args": { "type": "object", "description": "The arguments passed to the tool." }
              },
             "required": ["id", "name", "args"]
            }
          },
          "tool_call_id": {
              "type": "string",
              "description": "Present for 'tool' type messages, indicating which tool call this message is the result for."
          }
        },
        "required": ["type", "content", "checkpoint_id"]
      }
    }
  },
  "required": ["history"]
}
```

**Example Success Response:**

```json
{
  "history": [
    {
      "type": "human",
      "content": "Create a red cube.",
      "checkpoint_id": "f40e9a12-6789-4567-abcd-123456789abc"
    },
    {
      "type": "ai",
      "content": null,
      "checkpoint_id": "a987d23c-4321-5678-efgh-456789012def",
      "tool_calls": [
        {
          "id": "call_abc123",
          "name": "create_object",
          "args": {"type": "CUBE", "name": "MyCube"}
        },
        {
          "id": "call_def456",
          "name": "set_material",
          "args": {"object_name": "MyCube", "color": [1.0, 0.0, 0.0]}
        }
      ]
    },
    {
        "type": "tool",
        "content": "{\"status\": \"success\", \"object_name\": \"MyCube\"}",
        "checkpoint_id": "b123e456-7890-1234-ijkl-789012345ghi",
        "tool_call_id": "call_abc123"
    },
     {
        "type": "tool",
        "content": "{\"status\": \"success\"}",
        "checkpoint_id": "c234f567-8901-2345-mnop-890123456jkl",
        "tool_call_id": "call_def456"
    },
    {
        "type": "ai",
        "content": "Okay, I've created a red cube named 'MyCube'.",
        "checkpoint_id": "d345g678-9012-3456-qrst-901234567mno",
        "tool_calls": null
    }
  ]
}
```

---

## Endpoint: `/models`

**Method:** `GET`

**Description:** Retrieves a list of available models that can be used for processing messages.

**Response Body Schema (Status Code: 200 OK)**

```json
{
  "type": "object",
  "properties": {
    "models": {
      "type": "array",
      "description": "A list of available model names.",
      "items": {
        "type": "string"
      }
    }
  },
  "required": ["models"]
}
```

**Example Success Response:**

```json
{
  "models": ["gpt-4-turbo-preview", "gpt-3.5-turbo"]
}
```

---

## Endpoint: `/create-checkout-session`

**Method:** `POST`

**Description:** Creates a Stripe Checkout session for the authenticated user to initiate a subscription purchase. Returns a URL to redirect the user to the Stripe-hosted checkout page.

**Request Body:** None (user identity is determined from the authentication token).

**Response Body Schema (Status Code: 200 OK)**

```json
{
  "type": "object",
  "properties": {
    "url": {
      "type": "string",
      "format": "url",
      "description": "The URL to redirect the user to for completing the Stripe Checkout process."
    }
  },
  "required": ["url"]
}
```

**Example Success Response:**

```json
{
  "url": "https://checkout.stripe.com/pay/cs_test_a1b2c3d4..."
}
```

---

## Endpoint: `/subscription/status`

**Method:** `GET`

**Description:** Retrieves the current subscription status for the authenticated user. This status is determined by querying the application's database, which should be kept up-to-date via Stripe webhooks.

**Response Body Schema (Status Code: 200 OK)**

```json
{
  "type": "object",
  "properties": {
    "status": {
      "type": "string",
      "description": "The user's current subscription status (e.g., 'active', 'trialing', 'inactive', 'canceled', 'past_due')."
    }
  },
  "required": ["status"]
}
```

**Example Success Response (Active):**

```json
{
  "status": "active"
}
```

**Example Success Response (Inactive):**

```json
{
  "status": "inactive"
}
```

---

## Error Responses (Status Codes: 4xx, 5xx)

These apply to all endpoints when an error occurs (e.g., authentication failure, invalid input, server issues).

**Common Error Response Body Schema**

```json
{
  "type": "object",
  "properties": {
    "error": {
      "type": "string",
      "description": "A general description of the error (e.g., 'Unauthorized', 'Bad Request', 'Not Found', 'Internal Server Error')."
    },
    "details": {
        "type": "string",
        "description": "Optional additional details about the error."
    }
  },
  "required": ["error"]
}
```

**Example Error (401 Unauthorized):**

```json
{
  "error": "Unauthorized: Invalid token"
}
```

**Example Error (400 Bad Request):**

```json
{
  "error": "Bad Request: thread_id is required"
}
```

**Example Error (403 Forbidden - History Access):**

```json
{
  "error": "Forbidden: Access denied to this thread"
}
```

**Example Error (404 Not Found):**

```json
{
  "error": "Not Found"
}
```

**Example Error (500 Internal Server Error):**

```json
{
  "error": "Internal Server Error: Service configuration failed."
}
``` 