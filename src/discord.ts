import { chromium } from "playwright";
import type { AppConfig } from "./config.js";

const MESSAGE_BOX_SELECTOR = 'div[role="textbox"]';

export async function openDiscordForLogin(config: AppConfig): Promise<void> {
  if (!config.discordChannelUrl) {
    throw new Error("DISCORD_CHANNEL_URL is required");
  }

  const context = await chromium.launchPersistentContext(config.profileDir, {
    channel: undefined,
    headless: false
  });
  const page = context.pages()[0] ?? (await context.newPage());
  await page.goto(config.discordChannelUrl, { waitUntil: "domcontentloaded" });

  console.log("Discord opened.");
  console.log("Log in if needed, confirm you can see the target channel, then press Ctrl+C in this terminal.");
}

export async function postToDiscord(config: AppConfig, message: string): Promise<void> {
  if (!config.discordChannelUrl) {
    throw new Error("DISCORD_CHANNEL_URL is required");
  }

  if (!message.trim()) {
    throw new Error("Refusing to post an empty message");
  }

  const context = await chromium.launchPersistentContext(config.profileDir, {
    headless: config.headless
  });

  try {
    const page = context.pages()[0] ?? (await context.newPage());
    await page.goto(config.discordChannelUrl, { waitUntil: "domcontentloaded" });

    const messageBox = page.locator(MESSAGE_BOX_SELECTOR).last();
    await messageBox.waitFor({ state: "visible", timeout: 30000 });
    await messageBox.click();
    await page.keyboard.insertText(message);
    await page.keyboard.press("Enter");

    const firstLine = message.split("\n")[0] ?? "";
    if (firstLine) {
      await page.getByText(firstLine, { exact: false }).last().waitFor({ state: "visible", timeout: 30000 });
    }
  } finally {
    await context.close();
  }
}
