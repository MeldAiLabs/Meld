# Blender AI Chat Implementation Notes

This document provides technical details on the implementation of the AI Chat feature for Blender's Video Sequence Editor.

## Overview

The implementation consists of two main components:

1. **AI Chat Addon** (`ai_chat.py`): Implements the core AI chat functionality using Google's Gemini API with function calling capability.
2. **AI Chat Sequencer Integration** (`ai_chat_sequencer.py`): Integrates the AI chat into the VSE interface, adding a toggle button in the header.

## AI Chat Addon Implementation

The core AI chat addon (`ai_chat.py`) provides the following functionality:

### 1. Chat Interface

- Creates a panel in the sidebar of the Video Sequence Editor (accessible via the 'N' key)
- Provides a text input field for user messages
- Displays a history of messages between the user and AI
- Shows real-time streaming responses from the AI

### 2. Gemini API Integration

- Integrates with Google's Gemini API using the `google-generativeai` Python package
- Supports streaming responses for a more interactive experience
- Implements function calling to allow the AI to execute actions in Blender

### 3. Function Declarations

The addon defines several functions that the AI can call:

- `executeCode`: Execute Python code in Blender
- `getSceneInfo`: Get information about the current scene
- `getSequencerInfo`: Get information about the current sequencer strips

### 4. Execution Environment

- Function calls are executed in Blender's Python environment
- Results are returned to the AI and displayed in the chat
- Code execution is performed safely with output capturing

### 5. Data Management

- Chat history is stored in memory during a session
- API keys are stored in Blender's scene properties

## AI Chat Sequencer Integration Implementation

The sequencer integration addon (`ai_chat_sequencer.py`) handles the following:

### 1. Header Integration

- Modifies the Sequencer header to add an AI Chat toggle button
- Preserves all original functionality
- Stores and restores the original header draw function when unregistering

### 2. SpaceSequenceEditor Flag

- Adds a boolean property to the SpaceSequenceEditor class to track whether AI Chat is enabled
- This flag is used to determine whether to display the AI Chat interface

### 3. Toggle Functionality

- Implements an operator to toggle the AI Chat interface
- Updates the UI to reflect the current state

## Technical Considerations

### Threading

- API requests run in a separate thread to prevent blocking the UI
- Blender's timers are used to update the UI when new content is available
- The stop button allows cancelling ongoing API requests

### Dependencies

- The addon checks for the presence of the Google AI SDK
- If not installed, it provides a one-click install button
- Dependencies are installed using Python's pip in the Blender Python environment

### Error Handling

- Robust error handling for API failures
- Graceful degradation when dependencies are missing
- User feedback on execution errors

## Function Calling Implementation

The function calling implementation follows this flow:

1. User sends a message
2. Message is sent to Gemini API with function declarations
3. If Gemini decides to call a function:
   - Function call is received in the streaming response
   - The appropriate handler is executed in Blender
   - Results are sent back and displayed in the chat
4. Process continues until the response is complete

## UI Integration

The UI is designed to be consistent with Blender's interface:

- Uses standard Blender UI elements
- Fits into the existing Sequencer layout
- Follows Blender's UI color scheme and styling
- Provides familiar icons and controls

## Limitations

- The current implementation doesn't persist chat history between Blender sessions
- Function calling is limited to a predefined set of functions
- The UI area integration is not fully implemented yet
- The addon requires an internet connection and API key

## Future Improvements

- Persist chat history
- Add more function declarations for common VSE operations
- Implement the full area integration for a more seamless experience
- Add support for more AI models
- Add local LLM options for offline usage
- Implement history export/import 