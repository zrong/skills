#!/usr/bin/env node
/**
 * CLI tool for sending images via Feishu
 * Usage: node cli.js --image /path/to/image.png --to <user_open_id> [--text "optional message"]
 */

const { FeishuImage } = require('./index.js');
const path = require('path');

function showHelp() {
  console.log(`
Feishu Image Sender CLI

Usage:
  node cli.js --image <path> --to <user_id> [options]

Required:
  --image <path>      Path to the image file
  --to <user_id>      Recipient's Feishu open_id

Options:
  --text <message>    Optional text message to include
  --chat              Send to a chat group instead of user
  --help              Show this help message

Environment Variables:
  FEISHU_APP_ID       Your Feishu app ID
  FEISHU_APP_SECRET   Your Feishu app secret

Examples:
  # Send image to user
  node cli.js --image screenshot.png --to ou_123456789

  # Send with text
  node cli.js --image chart.png --to ou_123456789 --text "Here is the Q4 report"

  # Send to chat group
  node cli.js --image announcement.png --to oc_987654321 --chat
`);
}

function parseArgs(args) {
  const options = {
    image: null,
    to: null,
    text: null,
    chat: false
  };

  for (let i = 0; i < args.length; i++) {
    const arg = args[i];
    
    switch (arg) {
      case '--help':
      case '-h':
        showHelp();
        process.exit(0);
        break;
      case '--image':
      case '-i':
        options.image = args[++i];
        break;
      case '--to':
      case '-t':
        options.to = args[++i];
        break;
      case '--text':
        options.text = args[++i];
        break;
      case '--chat':
      case '-c':
        options.chat = true;
        break;
    }
  }

  return options;
}

async function main() {
  try {
    const options = parseArgs(process.argv.slice(2));

    // Validate required options
    if (!options.image || !options.to) {
      console.error('Error: --image and --to are required\n');
      showHelp();
      process.exit(1);
    }

    // Check if image file exists
    const fs = require('fs');
    if (!fs.existsSync(options.image)) {
      console.error(`Error: Image file not found: ${options.image}`);
      process.exit(1);
    }

    // Check environment variables
    if (!process.env.FEISHU_APP_ID || !process.env.FEISHU_APP_SECRET) {
      console.error('Error: FEISHU_APP_ID and FEISHU_APP_SECRET environment variables must be set');
      console.error('\nYou can set them like this:');
      console.error('  export FEISHU_APP_ID=your_app_id');
      console.error('  export FEISHU_APP_SECRET=your_app_secret');
      process.exit(1);
    }

    // Create sender instance
    const sender = new FeishuImage({
      appId: process.env.FEISHU_APP_ID,
      appSecret: process.env.FEISHU_APP_SECRET
    });

    // Send the image
    console.log(`Sending image: ${options.image}`);
    console.log(`To: ${options.to} (${options.chat ? 'chat' : 'user'})`);
    
    if (options.text) {
      console.log(`With text: ${options.text}`);
    }

    const messageId = await sender.sendImage({
      imagePath: options.image,
      receiveId: options.to,
      receiveType: options.chat ? 'chat' : 'user',
      text: options.text
    });

    console.log('\n✓ Image sent successfully!');
    console.log(`  Message ID: ${messageId}`);

  } catch (error) {
    console.error('\n✗ Failed to send image:');
    console.error(`  ${error.message}`);
    
    if (error.code) {
      console.error(`  Error code: ${error.code}`);
    }
    
    if (error.details) {
      console.error(`  Details:`, error.details);
    }
    
    process.exit(1);
  }
}

main();