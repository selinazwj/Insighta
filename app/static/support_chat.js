(function () {
  if (window.__insightaSupportChatLoaded) return;
  window.__insightaSupportChatLoaded = true;

  let threadId = null;
  let pollTimer = null;
  let isOpen = false;

  function esc(value) {
    return String(value || '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  function shortTime(value) {
    if (!value) return '';
    try {
      return new Date(value).toLocaleTimeString([], { hour: 'numeric', minute: '2-digit' });
    } catch {
      return '';
    }
  }

  function ensureUI() {
    if (document.getElementById('supportChatLauncher')) return;
    const launcher = document.createElement('button');
    launcher.type = 'button';
    launcher.className = 'support-chat-launcher';
    launcher.id = 'supportChatLauncher';
    launcher.setAttribute('aria-label', 'Open support chat');
    launcher.title = 'Support';
    launcher.innerHTML = `
      <svg viewBox="0 0 24 24" aria-hidden="true">
        <path d="M4 14v-2a8 8 0 0 1 16 0v2"></path>
        <path d="M18 19c0 1.1-.9 2-2 2h-3"></path>
        <rect x="2" y="12" width="4" height="6" rx="2"></rect>
        <rect x="18" y="12" width="4" height="6" rx="2"></rect>
      </svg>
    `;
    launcher.addEventListener('click', toggleSupportChat);

    const panel = document.createElement('section');
    panel.className = 'support-chat-panel';
    panel.id = 'supportChatPanel';
    panel.innerHTML = `
      <div class="support-chat-head">
        <div>
          <div class="support-chat-kicker">Live support</div>
          <div class="support-chat-title">Talk to our team</div>
          <div class="support-chat-hours" id="supportChatHours">Available 10am-5pm</div>
        </div>
        <button type="button" class="support-chat-close" aria-label="Close support chat" id="supportChatClose">x</button>
      </div>
      <div class="support-chat-messages" id="supportChatMessages">
        <div class="support-chat-empty">Open a chat and leave a message. Our team replies here during support hours.</div>
      </div>
      <form class="support-chat-form" id="supportChatForm">
        <textarea class="support-chat-input" id="supportChatInput" rows="1" placeholder="Type your message..." required></textarea>
        <button type="submit" class="support-chat-send" id="supportChatSend">Send</button>
      </form>
    `;

    document.body.appendChild(launcher);
    document.body.appendChild(panel);
    document.getElementById('supportChatClose').addEventListener('click', closeSupportChat);
    document.getElementById('supportChatForm').addEventListener('submit', sendSupportMessage);
    document.getElementById('supportChatInput').addEventListener('keydown', event => {
      if (event.key === 'Enter' && !event.shiftKey) {
        event.preventDefault();
        document.getElementById('supportChatForm').requestSubmit();
      }
    });
  }

  function renderMessages(messages) {
    const wrap = document.getElementById('supportChatMessages');
    if (!wrap) return;
    if (!messages || !messages.length) {
      wrap.innerHTML = '<div class="support-chat-empty">No messages yet. Tell us what you need help with.</div>';
      return;
    }
    wrap.innerHTML = messages.map(message => `
      <div class="support-chat-msg ${esc(message.sender_type)}">
        ${esc(message.body)}
        <span class="support-chat-meta">${esc(message.sender_type === 'admin' ? 'Support' : message.sender_type === 'user' ? 'You' : 'Insighta')} ${esc(shortTime(message.created_at))}</span>
      </div>
    `).join('');
    wrap.scrollTop = wrap.scrollHeight;
  }

  async function loadSupportThread() {
    const res = await fetch('/api/support/thread', { credentials: 'same-origin', cache: 'no-store' });
    if (!res.ok) throw new Error('Could not open support chat');
    const data = await res.json();
    threadId = data.thread && data.thread.id;
    document.getElementById('supportChatHours').textContent = `Available ${data.available_hours || '10am-5pm'}`;
    renderMessages(data.messages || []);
  }

  async function pollSupportMessages() {
    if (!isOpen || !threadId) return;
    try {
      const res = await fetch(`/api/support/messages?thread_id=${encodeURIComponent(threadId)}&_=${Date.now()}`, {
        credentials: 'same-origin',
        cache: 'no-store',
      });
      if (res.ok) renderMessages(await res.json());
    } catch {}
  }

  async function openSupportChat() {
    ensureUI();
    isOpen = true;
    document.getElementById('supportChatPanel').classList.add('open');
    document.getElementById('supportChatLauncher').style.display = 'none';
    try {
      await loadSupportThread();
      pollTimer = window.setInterval(pollSupportMessages, 5000);
    } catch (error) {
      document.getElementById('supportChatMessages').innerHTML =
        `<div class="support-chat-empty">${esc(error.message || 'Support chat is unavailable right now.')}</div>`;
    }
    document.getElementById('supportChatInput').focus();
  }

  function closeSupportChat() {
    isOpen = false;
    document.getElementById('supportChatPanel').classList.remove('open');
    document.getElementById('supportChatLauncher').style.display = '';
    if (pollTimer) window.clearInterval(pollTimer);
    pollTimer = null;
  }

  function toggleSupportChat() {
    if (isOpen) closeSupportChat();
    else openSupportChat();
  }

  window.openInsightaSupportChat = openSupportChat;
  window.closeInsightaSupportChat = closeSupportChat;

  async function sendSupportMessage(event) {
    event.preventDefault();
    const input = document.getElementById('supportChatInput');
    const send = document.getElementById('supportChatSend');
    const body = input.value.trim();
    if (!body) return;
    send.disabled = true;
    try {
      const res = await fetch('/api/support/messages', {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ thread_id: threadId, body }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || 'Message failed to send');
      threadId = data.thread && data.thread.id;
      input.value = '';
      await pollSupportMessages();
    } catch (error) {
      alert(error.message || 'Message failed to send');
    } finally {
      send.disabled = false;
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', ensureUI);
  } else {
    ensureUI();
  }
})();
