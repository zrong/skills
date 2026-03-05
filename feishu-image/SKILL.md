---
name: feishu-image
description: Upload and send images via Feishu (Lark) API. Use when the user asks to send screenshots, images, or any visual content through Feishu. This skill handles image upload to Feishu servers and message delivery. Required environment variables: FEISHU_APP_ID, FEISHU_APP_SECRET.
---

# Feishu Image Sender

Send images to Feishu conversations using the official Feishu API.

## When to use this skill

- User asks to "截图发给我" (send me a screenshot)
- User wants to share any image file through Feishu
- Need to send visual content (charts, diagrams, photos) to a Feishu conversation
- OpenClaw's built-in message tool fails to send images (returns success but image not delivered)

## Prerequisites

The following environment variables must be set:
- `FEISHU_APP_ID` - Your Feishu app ID
- `FEISHU_APP_SECRET` - Your Feishu app secret

The app must have the following permissions:
- `im:resource` - For uploading images
- `im:message` - For sending messages

## Usage

### Sending a local image file

```javascript
const FeishuImage = require('./feishu-image.js');

const sender = new FeishuImage({
  appId: process.env.FEISHU_APP_ID,
  appSecret: process.env.FEISHU_APP_SECRET
});

// Send image to a user
await sender.sendImage({
  imagePath: '/path/to/screenshot.png',
  receiveId: 'ou_xxxxxxxxxxxxxxxx',  // User's open_id
  receiveType: 'user'  // 'user' or 'chat'
});
```

### Sending with a text message

```javascript
await sender.sendImage({
  imagePath: '/path/to/chart.png',
  receiveId: 'ou_xxxxxxxxxxxxxxxx',
  receiveType: 'user',
  text: 'Here is the sales chart for Q4'  // Optional text message
});
```

## API Reference

### `new FeishuImage(config)`

Creates a new Feishu Image sender instance.

**Parameters:**
- `config.appId` (string, required): Feishu app ID
- `config.appSecret` (string, required): Feishu app secret
- `config.baseUrl` (string, optional): API base URL, defaults to 'https://open.feishu.cn/open-apis'

### `async sendImage(options)`

Uploads an image and sends it to a Feishu conversation.

**Parameters:**
- `options.imagePath` (string, required): Path to the local image file
- `options.receiveId` (string, required): Recipient's open_id (for users) or chat_id (for groups)
- `options.receiveType` (string, optional): 'user' or 'chat', defaults to 'user'
- `options.text` (string, optional): Optional text message to send with the image

**Returns:**
- `messageId` (string): The ID of the sent message

**Throws:**
- `FeishuAuthError`: Authentication failed (check app_id/app_secret)
- `FeishuUploadError`: Image upload failed
- `FeishuSendError`: Message sending failed
- `FileNotFoundError`: Image file not found

### `async getTenantToken()`

Gets a tenant access token from Feishu. Called automatically by other methods.

**Returns:**
- `token` (string): The tenant access token

### `async uploadImage(imagePath)`

Uploads an image to Feishu and returns the image_key.

**Parameters:**
- `imagePath` (string, required): Path to the local image file

**Returns:**
- `imageKey` (string): The Feishu image key

## Error Handling

All methods may throw the following errors:

```javascript
try {
  await sender.sendImage({ ... });
} catch (error) {
  if (error.code === 'FEISHU_AUTH_ERROR') {
    console.error('Authentication failed. Check FEISHU_APP_ID and FEISHU_APP_SECRET.');
  } else if (error.code === 'FEISHU_UPLOAD_ERROR') {
    console.error('Failed to upload image:', error.message);
  } else if (error.code === 'FEISHU_SEND_ERROR') {
    console.error('Failed to send message:', error.message);
  }
}
```

## Best Practices

1. **Environment variables**: Always use environment variables for credentials, never hardcode them
2. **Error handling**: Always wrap calls in try-catch blocks
3. **Image size**: Feishu has image size limits (check latest docs). Consider compressing large images
4. **Async/await**: All methods return Promises, use await or .catch()
5. **Token caching**: The SDK handles token caching automatically, don't manually manage tokens

## Troubleshooting

### "Authentication failed" error
- Check that `FEISHU_APP_ID` and `FEISHU_APP_SECRET` are set correctly
- Verify the app has the required permissions in Feishu developer console
- Check if the token has expired (should auto-refresh)

### "Upload failed" error  
- Check that the image file exists and is readable
- Verify the image format is supported (PNG, JPG, etc.)
- Check if the image file is too large

### Message not delivered
- Verify the `receiveId` is correct (user's open_id or chat_id)
- Check that the app has permission to message the recipient
- For group chats, verify the bot is a member of the chat

## Integration with OpenClaw

This skill is designed to work alongside OpenClaw's built-in Feishu integration:

- Use OpenClaw's `message` tool for simple text messages
- Use this skill when you need to send images or files

Example workflow:
```javascript
// 1. Take a screenshot using Peekaboo
// 2. Send via this skill
await feishuImage.sendImage({
  imagePath: '/Users/zrong/Pictures/Screenshots/screen.png',
  receiveId: 'ou_432bdac4d8ce6dbcf182015e79583419',
  text: 'Here is the screenshot you requested'
});
```

## See Also

- Feishu API Documentation: https://open.feishu.cn/document/
- OpenClaw Feishu Integration: (link to OpenClaw docs)
- Peekaboo Documentation: (link to Peekaboo docs for taking screenshots)