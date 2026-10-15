// AC-2: loadMessages request sequencing — rapid chat switches must show
// the final chat's messages, not a stale response from an earlier request.
// Requires at least 3 seeded chats in the DB; skips otherwise.
const { test, expect } = require('@playwright/test');

test('rapid chat switches show final chat messages', async ({ page }) => {
  await page.goto('/');
  await page.waitForSelector('#msgBody');
  await page.waitForTimeout(1500);

  const responses = [];
  page.on('response', async resp => {
    if (resp.url().includes('/api/messages')) {
      try {
        const data = await resp.json();
        responses.push({ url: resp.url(), data });
      } catch (e) { /* non-json, skip */ }
    }
  });

  const chatIds = await page.$$eval('#chatList .chat-item', items => items.map(el => el.dataset.chatId));
  test.skip(chatIds.length < 3, 'Need at least 3 seeded chats for race test');

  // Re-query each click — chat list re-renders on filterChat, so captured
  // ElementHandles go stale after the first click.
  for (const chatId of chatIds.slice(0, 3)) {
    await page.evaluate(id => {
      const el = document.querySelector(`#chatList .chat-item[data-chat-id="${id}"]`);
      if (el) el.click();
    }, chatId);
  }
  await page.waitForTimeout(2000);

  const last = responses[responses.length - 1];
  expect(last).toBeTruthy();
  const visibleSenders = await page.$$eval('#msgBody tr', rows =>
    rows.map(r => r.querySelector('.col-sender')?.textContent || '')
  );
  const lastSenders = (last.data.messages || []).map(m => m.sender_name || m.sender_username || '');
  for (const vs of visibleSenders.filter(s => s && s !== '-')) {
    expect(lastSenders.some(ls => ls.includes(vs) || vs.includes(ls))).toBeTruthy();
  }
});
