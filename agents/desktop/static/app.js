(() => {
  const state = {
    providers: [],
    defaultProvider: "gemini",
    conversations: [],
    activeId: null,
    sending: false,
  };

  const els = {
    sidebar: document.getElementById("sidebar"),
    backdrop: document.getElementById("sidebar-backdrop"),
    chatList: document.getElementById("chat-list"),
    thread: document.getElementById("thread"),
    provider: document.getElementById("provider"),
    model: document.getElementById("model"),
    search: document.getElementById("search"),
    prompt: document.getElementById("prompt"),
    form: document.getElementById("composer-form"),
    btnNew: document.getElementById("btn-new"),
    btnDelete: document.getElementById("btn-delete"),
    btnSend: document.getElementById("btn-send"),
    btnMenu: document.getElementById("btn-menu"),
    btnSettings: document.getElementById("btn-settings"),
    btnSettingsTop: document.getElementById("btn-settings-top"),
    btnSettingsClose: document.getElementById("btn-settings-close"),
    btnSettingsCancel: document.getElementById("btn-settings-cancel"),
    btnSettingsSave: document.getElementById("btn-settings-save"),
    settingsBackdrop: document.getElementById("settings-backdrop"),
    settingsProviders: document.getElementById("settings-providers"),
    settingsDefaultProvider: document.getElementById("settings-default-provider"),
    settingsPath: document.getElementById("settings-path"),
    settingsStatus: document.getElementById("settings-status"),
  };

  async function api(path, options = {}) {
    const res = await fetch(path, {
      headers: { "Content-Type": "application/json", ...(options.headers || {}) },
      ...options,
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || res.statusText || "Request failed");
    return data;
  }

  function currentProvider() {
    return els.provider.value || state.defaultProvider;
  }

  function syncSendEnabled() {
    els.btnSend.disabled = state.sending || !els.prompt.value.trim();
  }

  function fillModels() {
    const p = state.providers.find((x) => x.id === currentProvider());
    const models = (p && p.models) || [];
    const preferred = (p && p.default_model) || models[0] || "";
    els.model.innerHTML = "";
    if (!models.length) {
      const opt = document.createElement("option");
      opt.value = preferred;
      opt.textContent = preferred || "Model";
      els.model.appendChild(opt);
      return;
    }
    for (const m of models) {
      const opt = document.createElement("option");
      opt.value = m;
      opt.textContent = m;
      if (m === preferred) opt.selected = true;
      els.model.appendChild(opt);
    }
  }

  function closeSidebar() {
    els.sidebar.classList.remove("open");
    els.backdrop.hidden = true;
  }

  function openSidebar() {
    els.sidebar.classList.add("open");
    els.backdrop.hidden = false;
  }

  function renderChatList() {
    els.chatList.innerHTML = "";
    if (!state.conversations.length) {
      const empty = document.createElement("div");
      empty.className = "chat-item";
      empty.style.cursor = "default";
      empty.style.color = "var(--text-tertiary)";
      empty.textContent = "No chats yet";
      els.chatList.appendChild(empty);
      return;
    }
    for (const c of state.conversations) {
      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "chat-item" + (c.id === state.activeId ? " active" : "");
      btn.innerHTML = `<span class="title"></span>`;
      btn.querySelector(".title").textContent = c.title || "New chat";
      btn.addEventListener("click", () => {
        selectConversation(c.id);
        closeSidebar();
      });
      els.chatList.appendChild(btn);
    }
  }

  function escapeHtml(text) {
    return String(text)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function renderMarkdown(text) {
    const raw = String(text || "");
    if (typeof marked === "undefined" || typeof marked.parse !== "function") {
      return `<pre class="md-fallback">${escapeHtml(raw)}</pre>`;
    }
    try {
      marked.setOptions({
        gfm: true,
        breaks: true,
      });
      // Local desktop app — still strip script/iframe edge cases
      return marked
        .parse(raw)
        .replace(/<script[\s\S]*?>[\s\S]*?<\/script>/gi, "")
        .replace(/on\w+="[^"]*"/gi, "")
        .replace(/on\w+='[^']*'/gi, "");
    } catch (_err) {
      return `<pre class="md-fallback">${escapeHtml(raw)}</pre>`;
    }
  }

  async function copyText(text) {
    const value = String(text || "");
    try {
      if (navigator.clipboard && navigator.clipboard.writeText) {
        await navigator.clipboard.writeText(value);
        return true;
      }
    } catch (_err) {
      /* fall through */
    }
    try {
      const ta = document.createElement("textarea");
      ta.value = value;
      ta.setAttribute("readonly", "");
      ta.style.position = "fixed";
      ta.style.left = "-9999px";
      document.body.appendChild(ta);
      ta.select();
      const ok = document.execCommand("copy");
      document.body.removeChild(ta);
      return ok;
    } catch (_err) {
      return false;
    }
  }

  function enhanceCodeBlocks(container) {
    if (!container) return;
    container.querySelectorAll("pre").forEach((pre) => {
      if (pre.closest(".code-block")) return;

      const wrap = document.createElement("div");
      wrap.className = "code-block";
      pre.parentNode.insertBefore(wrap, pre);
      wrap.appendChild(pre);

      const toolbar = document.createElement("div");
      toolbar.className = "code-toolbar";

      const playBtn = document.createElement("button");
      playBtn.type = "button";
      playBtn.className = "code-play-btn";
      playBtn.title = "Run this command / script";
      playBtn.innerHTML = `<svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M8 5v14l11-7z"/></svg><span>Run</span>`;
      playBtn.addEventListener("click", async (e) => {
        e.preventDefault();
        e.stopPropagation();
        const code = pre.querySelector("code");
        const text = (code ? code.innerText : pre.innerText).replace(/\n$/, "");
        await runSingleBlock(text, wrap, playBtn);
      });

      const btn = document.createElement("button");
      btn.type = "button";
      btn.className = "code-copy-btn";
      btn.textContent = "Copy";
      btn.title = "Copy command / code";
      btn.addEventListener("click", async (e) => {
        e.preventDefault();
        e.stopPropagation();
        const code = pre.querySelector("code");
        const text = code ? code.innerText : pre.innerText;
        const ok = await copyText(text.replace(/\n$/, ""));
        btn.textContent = ok ? "Copied" : "Failed";
        setTimeout(() => {
          btn.textContent = "Copy";
        }, 1400);
      });

      toolbar.appendChild(playBtn);
      toolbar.appendChild(btn);
      wrap.appendChild(toolbar);
    });

    // Inline code: click to copy
    container.querySelectorAll("code").forEach((code) => {
      if (code.parentElement && code.parentElement.tagName === "PRE") return;
      if (code.dataset.copyable === "1") return;
      code.dataset.copyable = "1";
      code.classList.add("inline-copy");
      code.title = "Click to copy";
      code.addEventListener("click", async (e) => {
        e.preventDefault();
        e.stopPropagation();
        const ok = await copyText(code.innerText);
        const prev = code.getAttribute("data-tip");
        code.setAttribute("data-tip", ok ? "Copied" : "Failed");
        code.classList.add("copied");
        setTimeout(() => {
          code.classList.remove("copied");
          if (prev == null) code.removeAttribute("data-tip");
          else code.setAttribute("data-tip", prev);
        }, 1000);
      });
    });
  }

  function ensureRunOutput(wrap) {
    let out = wrap.querySelector(".run-output");
    if (!out) {
      out = document.createElement("pre");
      out.className = "run-output";
      wrap.appendChild(out);
    }
    return out;
  }

  async function runSingleBlock(command, wrap, playBtn) {
    const cmd = String(command || "").trim();
    if (!cmd) return;
    const out = ensureRunOutput(wrap);
    out.classList.add("running");
    playBtn.disabled = true;
    try {
      const prepared = await api("/api/run/prepare", {
        method: "POST",
        body: JSON.stringify({
          command: cmd,
          provider: currentProvider(),
          model: els.model.value,
        }),
      });
      let values = {};
      const needs = prepared.inputs || [];
      if (needs.length) {
        out.textContent = "Waiting for your input…";
        const inline = document.createElement("div");
        inline.className = "inline-ask";
        wrap.appendChild(inline);
        const answer = await promptInlineAsk(inline, {
          id: needs[0].id,
          label: needs.map((n) => n.label || n.id).join(", "),
          reason: "Needed before this command can run",
          options: [],
          allow_custom: true,
          secret: needs.some((n) => n.secret),
          fields: needs,
        });
        inline.remove();
        if (!answer) {
          out.textContent = "Cancelled.";
          out.classList.remove("running");
          return;
        }
        values = answer;
      } else if (!confirm(`Run this on your machine?\n\n${cmd.slice(0, 400)}${cmd.length > 400 ? "…" : ""}`)) {
        out.textContent = "Cancelled.";
        out.classList.remove("running");
        return;
      }
      out.textContent = "Running…";
      const data = await api("/api/run", {
        method: "POST",
        body: JSON.stringify({ command: prepared.command || cmd, inputs: values }),
      });
      const parts = [];
      parts.push(`$ ${data.command}`);
      if (data.stdout) parts.push(data.stdout.replace(/\n$/, ""));
      if (data.stderr) parts.push(data.stderr.replace(/\n$/, ""));
      parts.push(
        data.timed_out
          ? "[timed out]"
          : `[exit ${data.exit_code == null ? "?" : data.exit_code}]`
      );
      out.textContent = parts.join("\n");
      out.classList.toggle("failed", !data.ok);
      out.classList.remove("running");
    } catch (err) {
      out.textContent = `Error: ${err.message}`;
      out.classList.add("failed");
      out.classList.remove("running");
    } finally {
      playBtn.disabled = false;
    }
  }

  function promptInlineAsk(hostEl, spec) {
    return new Promise((resolve) => {
      hostEl.innerHTML = "";
      hostEl.hidden = false;
      const card = document.createElement("div");
      card.className = "inline-ask-card";
      const primaryId = spec.id || "value";
      const fields = spec.fields && spec.fields.length
        ? spec.fields
        : [{ id: primaryId, label: spec.label, secret: spec.secret }];
      const options = (spec.options || []).map((o) => String(o)).filter(Boolean);
      const allowCustom = spec.allow_custom !== false;
      const useChips = options.length > 0 && options.length <= 12;

      let html = `<div class="inline-ask-title">${escapeHtml(spec.label || "Your input")}</div>`;
      if (spec.reason) {
        html += `<div class="inline-ask-reason">${escapeHtml(spec.reason)}</div>`;
      }
      html += `<div class="inline-ask-fields"></div>`;
      html += `<div class="inline-ask-actions">
        <button type="button" class="btn-secondary inline-cancel">Cancel</button>
        <button type="button" class="btn-primary-solid inline-ok">Continue</button>
      </div>`;
      card.innerHTML = html;
      hostEl.appendChild(card);

      const fieldsWrap = card.querySelector(".inline-ask-fields");
      let selectEl = null;
      let customInput = null;
      let selectedChip = "";

      if (useChips) {
        let customLabel = null;
        const chips = document.createElement("div");
        chips.className = "option-chips";
        chips.setAttribute("role", "listbox");
        chips.setAttribute("aria-label", spec.label || "Options");
        options.forEach((opt) => {
          const btn = document.createElement("button");
          btn.type = "button";
          btn.className = "option-chip";
          btn.setAttribute("role", "option");
          btn.setAttribute("aria-selected", "false");
          btn.textContent = opt;
          btn.addEventListener("click", () => {
            chips.querySelectorAll(".option-chip").forEach((b) => {
              b.classList.remove("selected");
              b.setAttribute("aria-selected", "false");
            });
            btn.classList.add("selected");
            btn.setAttribute("aria-selected", "true");
            selectedChip = opt;
            if (customInput) customInput.value = "";
          });
          btn.addEventListener("dblclick", () => {
            selectedChip = opt;
            finish();
          });
          chips.appendChild(btn);
        });
        fieldsWrap.appendChild(chips);

        if (allowCustom) {
          customLabel = document.createElement("label");
          customLabel.className = "input-field custom-value-field";
          customLabel.innerHTML = `<span class="input-label">Or type a custom value</span>`;
          customInput = document.createElement("input");
          customInput.type = spec.secret ? "password" : "text";
          customInput.name = `${primaryId}_custom`;
          customInput.placeholder = "Custom value";
          customInput.autocomplete = "off";
          customInput.addEventListener("input", () => {
            if (customInput.value.trim()) {
              selectedChip = "";
              chips.querySelectorAll(".option-chip").forEach((b) => {
                b.classList.remove("selected");
                b.setAttribute("aria-selected", "false");
              });
            }
          });
          customLabel.appendChild(customInput);
          fieldsWrap.appendChild(customLabel);
        }
      } else if (options.length) {
        const label = document.createElement("label");
        label.className = "input-field";
        label.innerHTML = `<span class="input-label">Select an option</span>`;
        selectEl = document.createElement("select");
        selectEl.className = "inline-ask-select";
        selectEl.name = primaryId;
        const placeholder = document.createElement("option");
        placeholder.value = "";
        placeholder.textContent = `Choose… (${options.length})`;
        placeholder.disabled = true;
        placeholder.selected = true;
        selectEl.appendChild(placeholder);
        options.forEach((opt) => {
          const o = document.createElement("option");
          o.value = opt;
          o.textContent = opt;
          selectEl.appendChild(o);
        });
        if (allowCustom) {
          const customOpt = document.createElement("option");
          customOpt.value = "__custom__";
          customOpt.textContent = "Custom value…";
          selectEl.appendChild(customOpt);
        }
        label.appendChild(selectEl);
        fieldsWrap.appendChild(label);

        if (allowCustom) {
          const customLabel = document.createElement("label");
          customLabel.className = "input-field custom-value-field";
          customLabel.hidden = true;
          customLabel.innerHTML = `<span class="input-label">Custom value</span>`;
          customInput = document.createElement("input");
          customInput.type = spec.secret ? "password" : "text";
          customInput.name = `${primaryId}_custom`;
          customInput.placeholder = "Type a custom value";
          customInput.autocomplete = "off";
          customLabel.appendChild(customInput);
          fieldsWrap.appendChild(customLabel);
          selectEl.addEventListener("change", () => {
            const isCustom = selectEl.value === "__custom__";
            customLabel.hidden = !isCustom;
            if (isCustom) customInput.focus();
          });
        }
      } else {
        fields.forEach((field) => {
          const label = document.createElement("label");
          label.className = "input-field";
          label.innerHTML = `<span class="input-label">${escapeHtml(field.label || field.id)}</span>`;
          const input = document.createElement("input");
          input.name = field.id;
          input.type = field.secret || spec.secret ? "password" : "text";
          input.placeholder = field.placeholder || "Enter a value";
          input.autocomplete = "off";
          label.appendChild(input);
          fieldsWrap.appendChild(label);
        });
      }

      const collect = () => {
        const values = {};
        if (useChips) {
          const custom = customInput ? customInput.value.trim() : "";
          values[primaryId] = custom || selectedChip;
        } else if (selectEl) {
          let v = selectEl.value;
          if (v === "__custom__") {
            v = customInput ? customInput.value.trim() : "";
          }
          values[primaryId] = v;
        } else {
          fieldsWrap.querySelectorAll("input").forEach((input) => {
            values[input.name] = input.value.trim();
          });
        }
        return values;
      };

      const finish = () => {
        const values = collect();
        const v = values[primaryId] || Object.values(values).find((x) => x);
        if (!v) {
          card.classList.add("shake");
          setTimeout(() => card.classList.remove("shake"), 360);
          return;
        }
        resolve(values);
      };

      card.querySelector(".inline-cancel").addEventListener("click", () => resolve(null));
      card.querySelector(".inline-ok").addEventListener("click", finish);
      card.addEventListener("keydown", (e) => {
        if (e.key === "Enter" && !e.shiftKey) {
          e.preventDefault();
          finish();
        }
      });

      if (useChips) {
        const firstChip = fieldsWrap.querySelector(".option-chip");
        if (firstChip) firstChip.focus();
      } else if (selectEl) selectEl.focus();
      else {
        const first = fieldsWrap.querySelector("input");
        if (first) first.focus();
      }
    });
  }

  function promptForInputs(opts) {
    const backdrop = document.getElementById("inputs-backdrop");
    const form = document.getElementById("inputs-form");
    if (!backdrop || !form) return Promise.resolve(null);
    return new Promise((resolve) => {
      const titleEl = document.getElementById("inputs-title");
      const introEl = document.getElementById("inputs-intro");
      const btnClose = document.getElementById("btn-inputs-close");
      const btnCancel = document.getElementById("btn-inputs-cancel");
      titleEl.textContent = opts.title || "Inputs needed";
      introEl.textContent = opts.intro || "";
      form.innerHTML = "";
      (opts.inputs || []).forEach((field) => {
        const label = document.createElement("label");
        label.className = "input-field";
        label.innerHTML = `
          <span class="input-label">${escapeHtml(field.label || field.id)}</span>
          ${field.reason ? `<span class="input-reason">${escapeHtml(field.reason)}</span>` : ""}
          <input name="${escapeHtml(field.id)}" type="${field.secret ? "password" : "text"}"
            placeholder="${escapeHtml(field.placeholder || "")}" value="${escapeHtml(field.default || "")}"
            ${field.required ? "required" : ""} autocomplete="off" />`;
        form.appendChild(label);
      });
      const close = (v) => {
        backdrop.hidden = true;
        resolve(v);
      };
      form.onsubmit = (e) => {
        e.preventDefault();
        const data = {};
        (opts.inputs || []).forEach((field) => {
          const el = form.elements.namedItem(field.id);
          data[field.id] = el ? String(el.value || "").trim() : "";
        });
        close(data);
      };
      btnClose.onclick = () => close(null);
      btnCancel.onclick = () => close(null);
      backdrop.hidden = false;
    });
  }

  function isTransientStreamError(err) {
    const m = String((err && err.message) || err || "");
    return /input stream|network error|failed to fetch|load failed|connection reset|body stream|networkerror|premature/i.test(
      m
    );
  }

  function sleep(ms) {
    return new Promise((resolve) => setTimeout(resolve, ms));
  }

  async function streamScriptSteps(steps, values, panelEl, attempt = 0) {
    try {
      await streamScriptStepsOnce(steps, values, panelEl);
    } catch (err) {
      if (attempt < 3 && isTransientStreamError(err)) {
        const status = panelEl.querySelector(".script-status");
        if (status) {
          status.classList.remove("failed");
          status.innerHTML = `<span class="script-status-dot" aria-hidden="true"></span><span class="script-status-text">Connection dropped — retrying (${attempt + 1}/3)…</span>`;
        }
        await sleep(700 * (attempt + 1));
        return streamScriptSteps(steps, values, panelEl, attempt + 1);
      }
      throw err;
    }
  }

  async function streamScriptStepsOnce(steps, values, panelEl) {
    const known = { ...(values || {}) };
    const res = await fetch("/api/run/script/stream", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
      },
      body: JSON.stringify({
        steps,
        values: known,
        provider: currentProvider(),
        model: els.model.value,
        stop_on_error: true,
        pause_on_ask: true,
        analyze_output: true,
      }),
      cache: "no-store",
    });
    if (!res.ok) {
      const data = await res.json().catch(() => ({}));
      throw new Error(data.error || res.statusText || "Run failed");
    }

    const reader = res.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    let listEl = panelEl.querySelector(".script-steps");

    const setStatus = (text, failed) => {
      let status = panelEl.querySelector(".script-status");
      if (!status) {
        status = document.createElement("div");
        status.className = "script-status";
        panelEl.prepend(status);
      }
      status.classList.toggle("failed", !!failed);
      status.innerHTML = `<span class="script-status-dot" aria-hidden="true"></span><span class="script-status-text"></span>`;
      status.querySelector(".script-status-text").textContent = text;
    };

    const setStepState = (li, state) => {
      if (!li) return;
      li.classList.remove("active", "ok", "fail", "awaiting-input", "queued", "analyzing");
      if (state) li.classList.add(state);
      const el = li.querySelector(".step-state");
      if (!el) return;
      const labels = {
        queued: "Queued",
        active: "Running",
        analyzing: "AI…",
        ok: "Done",
        fail: "Failed",
        "awaiting-input": "Needs input",
      };
      el.textContent = labels[state] || "";
    };

    const setStepActivity = (li, message) => {
      if (!li) return;
      let row = li.querySelector(".step-activity");
      if (!row) {
        row = document.createElement("div");
        row.className = "step-activity";
        row.innerHTML = `<span class="step-activity-spinner" aria-hidden="true"></span><span class="step-activity-text"></span>`;
        const head = li.querySelector(".step-head");
        if (head && head.nextSibling) li.insertBefore(row, head.nextSibling);
        else li.appendChild(row);
      }
      const text = String(message || "").trim();
      if (!text) {
        row.hidden = true;
        row.querySelector(".step-activity-text").textContent = "";
        return;
      }
      row.hidden = false;
      row.querySelector(".step-activity-text").textContent = text;
      li.scrollIntoView({ behavior: "smooth", block: "nearest" });
    };

    const ensureList = (planSteps) => {
      if (listEl && listEl.isConnected) return listEl;
      listEl = document.createElement("ol");
      listEl.className = "script-steps";
      (planSteps || []).forEach((step, i) => {
        const li = document.createElement("li");
        li.dataset.index = String(i);
        li.className = "queued";
        const isUi = step.type === "ui";
        const label = isUi
          ? step.ask || step.input_id || "Choose a value"
          : step.cmd || "";
        li.innerHTML = `
          <div class="step-head">
            <span class="step-badge">${i + 1}</span>
            <div class="step-main">
              <span class="step-kind">${isUi ? "Ask" : "Run"}</span>
              <code class="step-cmd"></code>
            </div>
            <span class="step-state">Queued</span>
          </div>
          <div class="step-activity" hidden>
            <span class="step-activity-spinner" aria-hidden="true"></span>
            <span class="step-activity-text"></span>
          </div>
          <pre class="step-out" hidden></pre>
          <div class="step-ask" hidden></div>`;
        li.querySelector(".step-cmd").textContent = label;
        listEl.appendChild(li);
      });
      const status = panelEl.querySelector(".script-status");
      panelEl.querySelectorAll(".inline-ask").forEach((el) => el.remove());
      if (status && status.nextSibling) {
        panelEl.insertBefore(listEl, status.nextSibling);
      } else {
        panelEl.appendChild(listEl);
      }
      return listEl;
    };

    const askHostForStep = (index) => {
      ensureList(steps);
      let idx = Number.isFinite(Number(index)) ? Number(index) : 0;
      let li = listEl.querySelector(`li[data-index="${idx}"]`);
      if (!li) {
        li =
          listEl.querySelector("li.active") ||
          listEl.querySelector("li:last-child");
      }
      if (!li) {
        const host = document.createElement("div");
        host.className = "inline-ask";
        panelEl.appendChild(host);
        return host;
      }
      setStepState(li, "awaiting-input");
      setStepActivity(li, "");
      let host = li.querySelector(".step-ask");
      if (!host) {
        host = document.createElement("div");
        host.className = "step-ask";
        li.appendChild(host);
      }
      host.hidden = false;
      host.scrollIntoView({ behavior: "smooth", block: "nearest" });
      return host;
    };

    let streamTerminal = false;
    try {
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const parts = buffer.split("\n\n");
        buffer = parts.pop() || "";
        for (const part of parts) {
          for (const rawLine of part.split("\n")) {
            const line = rawLine.trim();
            if (!line.startsWith("data:")) continue;
            let payload;
            try {
              payload = JSON.parse(line.slice(5).trim());
            } catch (_e) {
              continue;
            }
            if (payload.type === "plan") {
              setStatus(
                `Running ${(payload.steps || []).length} steps — asks appear on the step that needs them`
              );
              const old = panelEl.querySelector(".script-steps");
              if (old) old.remove();
              listEl = null;
              ensureList(payload.steps || steps);
            } else if (payload.type === "step_start") {
              ensureList(steps);
              const li = listEl.querySelector(`li[data-index="${payload.index}"]`);
              if (li) {
                setStepState(li, "active");
                const cmdEl = li.querySelector(".step-cmd");
                if (cmdEl && payload.cmd) cmdEl.textContent = payload.cmd;
                setStepActivity(li, payload.message || "Running command…");
                setStatus(payload.message || `Running step ${payload.index + 1}…`);
              }
            } else if (payload.type === "step_progress") {
              ensureList(steps);
              const li = listEl.querySelector(`li[data-index="${payload.index}"]`);
              if (li) {
                if (payload.clear || !payload.message) {
                  setStepActivity(li, "");
                } else {
                  setStepState(li, "analyzing");
                  setStepActivity(li, payload.message);
                  setStatus(payload.message);
                }
              }
            } else if (payload.type === "step_done") {
              ensureList(steps);
              const li = listEl.querySelector(`li[data-index="${payload.index}"]`);
              if (!li) continue;
              setStepState(li, payload.ok ? "ok" : "fail");
              setStepActivity(li, "");
              const pre = li.querySelector(".step-out");
              const bits = [];
              if (payload.stdout) bits.push(payload.stdout);
              if (payload.stderr) bits.push(payload.stderr);
              bits.push(
                payload.timed_out
                  ? "[timed out]"
                  : `[exit ${payload.exit_code == null ? "?" : payload.exit_code}]`
              );
              pre.hidden = false;
              pre.textContent = bits.join("\n");
            } else if (payload.type === "cleanup_registered") {
              setStatus(`Will auto-revert later: ${payload.description || "lab change"}`);
            } else if (payload.type === "need_input") {
              streamTerminal = true;
              setStatus("Waiting for your choice on this step…");
              const stepIndex =
                payload.after_step != null ? payload.after_step : payload.index;
              const askHost = askHostForStep(stepIndex);
              const answer = await promptInlineAsk(askHost, {
                id: payload.id,
                label: payload.label || payload.id,
                reason: payload.reason || "",
                options: payload.options || [],
                allow_custom: payload.allow_custom !== false,
                secret: !!payload.secret,
              });
              askHost.innerHTML = "";
              askHost.hidden = true;
              const awaiting = askHost.closest("li");
              if (awaiting) setStepState(awaiting, "queued");
              if (!answer) {
                setStatus("Stopped — input cancelled.", true);
                return;
              }
              if (payload.confirm_continue) {
                const v = String(answer[payload.id] || Object.values(answer)[0] || "").toLowerCase();
                if (v !== "yes" && v !== "y") {
                  setStatus("Stopped — you answered no.", true);
                  return;
                }
                const remaining = (payload.remaining || []).map((s, i) =>
                  i === 0 ? { ...s, ask: "" } : s
                );
                await streamScriptSteps(remaining, { ...known, ...(payload.values || {}) }, panelEl);
                return;
              }
              Object.assign(known, payload.values || {}, answer);
              const remaining = payload.remaining || [];
              let nextSteps = remaining;
              if (remaining[0] && remaining[0].type === "ui") {
                nextSteps = remaining.slice(1);
              }
              await streamScriptSteps(nextSteps, known, panelEl);
              return;
            } else if (payload.type === "need_confirm") {
              streamTerminal = true;
              const ok = confirm(`${payload.ask}\n\n${payload.cmd || ""}`);
              if (!ok) return;
              const remaining = (payload.remaining || []).map((s, i) =>
                i === 0 ? { ...s, ask: "" } : s
              );
              await streamScriptSteps(remaining, known, panelEl);
              return;
            } else if (payload.type === "stopped") {
              streamTerminal = true;
              setStatus(`Stopped at step ${payload.index + 1} (command failed).`, true);
            } else if (payload.type === "finished") {
              streamTerminal = true;
              const cur = panelEl.querySelector(".script-status-text");
              const t = cur ? cur.textContent || "" : "";
              if (!/Stopped|cancelled/i.test(t)) {
                const cleaned = (payload.cleanup_results || []).length;
                setStatus(
                  cleaned
                    ? `Script finished — reverted ${cleaned} lab change(s).`
                    : "Script finished."
                );
              }
            } else if (payload.type === "error") {
              streamTerminal = true;
              throw new Error(payload.error || "script error");
            }
          }
        }
      }
    } catch (err) {
      if (isTransientStreamError(err)) throw err;
      throw err;
    }
    if (!streamTerminal) {
      throw new TypeError("Error in input stream");
    }
  }

  async function runScriptFromMessage(sourceText, panelEl, btn) {
    const source = String(sourceText || "").trim();
    if (!source) return;
    const actions = btn.closest(".msg-actions");
    let loader = actions && actions.querySelector(".plan-loader");
    if (actions && !loader) {
      loader = document.createElement("div");
      loader.className = "plan-loader";
      loader.setAttribute("role", "status");
      loader.setAttribute("aria-live", "polite");
      loader.innerHTML = `<span class="plan-loader-spinner" aria-hidden="true"></span><span class="plan-loader-text">Building command plan…</span>`;
      actions.appendChild(loader);
    }
    btn.disabled = true;
    if (loader) loader.hidden = false;
    panelEl.hidden = false;
    panelEl.innerHTML = `<div class="script-status"><span class="script-status-dot" aria-hidden="true"></span><span class="script-status-text">AI is building the command plan…</span></div>`;
    try {
      let plan = null;
      let lastErr = null;
      for (let attempt = 0; attempt < 3; attempt++) {
        try {
          plan = await api("/api/run/plan", {
            method: "POST",
            body: JSON.stringify({
              source,
              provider: currentProvider(),
              model: els.model.value,
            }),
          });
          lastErr = null;
          break;
        } catch (err) {
          lastErr = err;
          if (attempt < 2 && isTransientStreamError(err)) {
            if (loader) {
              const t = loader.querySelector(".plan-loader-text");
              if (t) t.textContent = `Retrying plan (${attempt + 1}/3)…`;
            }
            await sleep(700 * (attempt + 1));
            continue;
          }
          throw err;
        }
      }
      if (!plan) throw lastErr || new Error("Plan failed");
      const steps = plan.steps || [];
      if (!steps.length) throw new Error("No steps in plan");
      if (loader) loader.hidden = true;
      panelEl.innerHTML = `<div class="script-status"><span class="script-status-dot" aria-hidden="true"></span><span class="script-status-text">Starting ${steps.length} steps…</span></div>`;
      await streamScriptSteps(steps, {}, panelEl);
    } catch (err) {
      panelEl.innerHTML = `<div class="script-status failed"><span class="script-status-dot" aria-hidden="true"></span><span class="script-status-text">Error: ${escapeHtml(err.message)}</span></div>`;
    } finally {
      btn.disabled = false;
      if (loader) {
        loader.hidden = true;
        loader.innerHTML = `<span class="plan-loader-spinner" aria-hidden="true"></span><span class="plan-loader-text">Building command plan…</span>`;
      }
    }
  }

  function messageRow(role, content, { pending = false } = {}) {
    const row = document.createElement("article");
    row.className = `msg-row ${role}` + (pending ? " pending" : "");
    const avatarLabel = role === "assistant" ? "" : "";
    row.innerHTML = `
      <div class="msg-inner">
        <div class="msg-avatar">${avatarLabel}</div>
        <div class="msg-body">
          <div class="msg-content"></div>
          <div class="msg-actions" hidden></div>
          <div class="script-panel" hidden></div>
        </div>
      </div>`;
    const body = row.querySelector(".msg-content");
    const actions = row.querySelector(".msg-actions");
    const scriptPanel = row.querySelector(".script-panel");
    if (pending) {
      body.innerHTML = `<span class="typing" aria-label="Thinking"><i></i><i></i><i></i></span>`;
    } else if (role === "assistant") {
      body.classList.add("md");
      body.innerHTML = renderMarkdown(content);
      enhanceCodeBlocks(body);
      const hasCode = body.querySelectorAll("pre").length > 0;
      if (hasCode) {
        actions.hidden = false;
        const runAll = document.createElement("button");
        runAll.type = "button";
        runAll.className = "msg-run-all";
        runAll.innerHTML = `<svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M8 5v14l11-7z"/></svg> Run script (AI ordered)`;
        runAll.title = "Extract commands, order them with AI, then run in sequence";
        runAll.addEventListener("click", () => {
          runScriptFromMessage(content, scriptPanel, runAll);
        });
        actions.appendChild(runAll);
      }
    } else {
      body.textContent = content;
    }
    return row;
  }

  function renderThread(conversation) {
    els.thread.innerHTML = "";
    const messages = (conversation && conversation.messages) || [];
    if (!messages.length) {
      const empty = document.createElement("div");
      empty.className = "empty-state";
      empty.innerHTML = `
        <div class="empty-logo"><img src="/static/hatsoff-mark.svg" alt="HatsOff" /></div>
        <h1>HatsOff</h1>
        <p style="color:var(--text-secondary);margin-top:0.5rem">by Tahir — Kali Linux pentest assistant</p>`;
      els.thread.appendChild(empty);
      return;
    }
    for (const msg of messages) {
      els.thread.appendChild(messageRow(msg.role, msg.content));
    }
    els.thread.scrollTop = els.thread.scrollHeight;
  }

  async function loadProviders() {
    const data = await api("/api/providers");
    state.providers = data.providers || [];
    state.defaultProvider = data.default_provider || "gemini";
    els.provider.innerHTML = "";
    for (const p of state.providers) {
      const opt = document.createElement("option");
      opt.value = p.id;
      opt.textContent = p.id === "chatgpt" ? "ChatGPT" : p.id.charAt(0).toUpperCase() + p.id.slice(1);
      if (p.id === state.defaultProvider) opt.selected = true;
      els.provider.appendChild(opt);
    }
    fillModels();
  }

  async function loadConversations(q) {
    const qs = q ? `?q=${encodeURIComponent(q)}` : "";
    const data = await api(`/api/conversations${qs}`);
    state.conversations = data.conversations || [];
    renderChatList();
  }

  async function selectConversation(id) {
    state.activeId = id;
    renderChatList();
    const conv = await api(`/api/conversations/${id}`);
    if (conv.provider) els.provider.value = conv.provider;
    fillModels();
    if (conv.model) {
      const exists = [...els.model.options].some((o) => o.value === conv.model);
      if (!exists && conv.model) {
        const opt = document.createElement("option");
        opt.value = conv.model;
        opt.textContent = conv.model;
        els.model.appendChild(opt);
      }
      els.model.value = conv.model;
    }
    renderThread(conv);
  }

  async function createChat() {
    const conv = await api("/api/conversations", {
      method: "POST",
      body: JSON.stringify({
        provider: currentProvider(),
        model: els.model.value,
      }),
    });
    await loadConversations(els.search.value);
    await selectConversation(conv.id);
    els.prompt.focus();
    closeSidebar();
  }

  async function deleteChat() {
    if (!state.activeId) return;
    if (!confirm("Delete this chat permanently?")) return;
    await api(`/api/conversations/${state.activeId}`, { method: "DELETE" });
    state.activeId = null;
    await loadConversations(els.search.value);
    renderThread(null);
  }

  function paintAssistantReply(row, finalText) {
    const bodyEl = row.querySelector(".msg-content");
    const actions = row.querySelector(".msg-actions");
    const scriptPanel = row.querySelector(".script-panel");
    row.classList.remove("pending");
    bodyEl.classList.remove("streaming");
    bodyEl.classList.add("md");
    bodyEl.innerHTML = renderMarkdown(finalText || "");
    enhanceCodeBlocks(bodyEl);
    if (actions && scriptPanel) {
      actions.hidden = true;
      actions.innerHTML = "";
      if (bodyEl.querySelector("pre")) {
        actions.hidden = false;
        const runAll = document.createElement("button");
        runAll.type = "button";
        runAll.className = "msg-run-all";
        runAll.innerHTML = `<svg width="12" height="12" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M8 5v14l11-7z"/></svg> Run script (AI ordered)`;
        runAll.title = "Extract commands, order them with AI, then run in sequence";
        runAll.addEventListener("click", () => {
          runScriptFromMessage(finalText, scriptPanel, runAll);
        });
        actions.appendChild(runAll);
      }
    }
    els.thread.scrollTop = els.thread.scrollHeight;
  }

  async function sendMessage(event) {
    event.preventDefault();
    if (state.sending) return;
    const text = els.prompt.value.trim();
    if (!text) return;

    if (!state.activeId) await createChat();

    state.sending = true;
    syncSendEnabled();
    els.prompt.value = "";
    autoGrow();

    const empty = els.thread.querySelector(".empty-state");
    if (empty) empty.remove();

    els.thread.appendChild(messageRow("user", text));
    const pending = messageRow("assistant", "", { pending: true });
    els.thread.appendChild(pending);
    els.thread.scrollTop = els.thread.scrollHeight;

    try {
      const data = await api(`/api/conversations/${state.activeId}/messages`, {
        method: "POST",
        body: JSON.stringify({
          content: text,
          provider: currentProvider(),
          model: els.model.value,
        }),
      });
      const reply =
        (data.assistant_message && data.assistant_message.content) || "";
      paintAssistantReply(pending, reply);
      if (data.conversation) {
        const idx = state.conversations.findIndex(
          (c) => c.id === data.conversation.id
        );
        if (idx >= 0) {
          state.conversations[idx] = {
            ...state.conversations[idx],
            title: data.conversation.title,
            updated_at: data.conversation.updated_at,
          };
          renderChatList();
        } else {
          await loadConversations(els.search.value);
        }
      }
    } catch (err) {
      pending.classList.remove("pending");
      const bodyEl = pending.querySelector(".msg-content");
      bodyEl.classList.remove("streaming", "md");
      bodyEl.textContent = `Error: ${err.message}`;
    } finally {
      state.sending = false;
      syncSendEnabled();
      els.prompt.focus();
    }
  }

  function autoGrow() {
    els.prompt.style.height = "auto";
    els.prompt.style.height = Math.min(els.prompt.scrollHeight, 200) + "px";
  }

  function showSettingsStatus(msg, isError) {
    els.settingsStatus.hidden = false;
    els.settingsStatus.textContent = msg;
    els.settingsStatus.classList.toggle("error", !!isError);
  }

  function closeSettings() {
    els.settingsBackdrop.hidden = true;
    els.settingsStatus.hidden = true;
  }

  function renderSettingsForm(data) {
    els.settingsPath.textContent = data.config_path
      ? `Saved to ${data.config_path}`
      : "";
    els.settingsDefaultProvider.innerHTML = "";
    for (const p of data.providers || []) {
      const opt = document.createElement("option");
      opt.value = p.id;
      opt.textContent = p.label || p.id;
      if (p.id === data.default_provider) opt.selected = true;
      els.settingsDefaultProvider.appendChild(opt);
    }

    els.settingsProviders.innerHTML = "";
    for (const p of data.providers || []) {
      const card = document.createElement("section");
      card.className = "provider-card";
      card.dataset.providerId = p.id;
      const placeholder = p.api_key_configured
        ? `Configured (${p.api_key_masked}) — paste new value to replace`
        : p.id === "ollama"
          ? "http://localhost:11434"
          : "Paste key…";
      card.innerHTML = `
        <div class="provider-card-head">
          <strong></strong>
          <span class="badge ${p.api_key_configured ? "ok" : ""}"></span>
        </div>
        <p class="provider-help"></p>
        <div class="provider-grid">
          <label>
            <span></span>
            <input type="password" class="settings-key" autocomplete="off" spellcheck="false" />
          </label>
        </div>
        <div class="provider-grid two">
          <label>
            <span>Default model</span>
            <input type="text" class="settings-default-model" />
          </label>
          <label>
            <span>Models (comma-separated)</span>
            <input type="text" class="settings-models" />
          </label>
        </div>`;
      card.querySelector("strong").textContent = p.label || p.id;
      const badge = card.querySelector(".badge");
      badge.textContent = p.api_key_configured ? "Key set" : "Not set";
      card.querySelector(".provider-help").textContent = p.help || "";
      card.querySelector(".provider-grid span").textContent = p.key_label || "API key";
      const keyInput = card.querySelector(".settings-key");
      keyInput.placeholder = placeholder;
      if (p.id === "ollama" && p.api_key_configured) {
        keyInput.type = "text";
      }
      card.querySelector(".settings-default-model").value = p.default_model || "";
      card.querySelector(".settings-models").value = (p.models || []).join(", ");
      els.settingsProviders.appendChild(card);
    }
  }

  async function openSettings() {
    closeSidebar();
    els.settingsBackdrop.hidden = false;
    els.settingsStatus.hidden = true;
    try {
      const data = await api("/api/settings");
      renderSettingsForm(data);
    } catch (err) {
      showSettingsStatus(err.message, true);
    }
  }

  async function saveSettings() {
    els.btnSettingsSave.disabled = true;
    try {
      const providers = [...els.settingsProviders.querySelectorAll(".provider-card")].map(
        (card) => {
          const keyVal = card.querySelector(".settings-key").value.trim();
          return {
            id: card.dataset.providerId,
            api_key: keyVal || undefined,
            default_model: card.querySelector(".settings-default-model").value.trim(),
            models: card.querySelector(".settings-models").value,
          };
        }
      );
      const data = await api("/api/settings", {
        method: "PUT",
        body: JSON.stringify({
          default_provider: els.settingsDefaultProvider.value,
          providers,
        }),
      });
      renderSettingsForm(data);
      await loadProviders();
      if (data.default_provider) {
        els.provider.value = data.default_provider;
        fillModels();
      }
      showSettingsStatus("Settings saved.", false);
    } catch (err) {
      showSettingsStatus(err.message, true);
    } finally {
      els.btnSettingsSave.disabled = false;
    }
  }

  els.provider.addEventListener("change", fillModels);
  els.search.addEventListener("input", () => {
    clearTimeout(els.search._t);
    els.search._t = setTimeout(() => loadConversations(els.search.value), 200);
  });
  els.btnNew.addEventListener("click", createChat);
  els.btnDelete.addEventListener("click", deleteChat);
  els.btnMenu.addEventListener("click", openSidebar);
  els.backdrop.addEventListener("click", closeSidebar);
  els.form.addEventListener("submit", sendMessage);
  els.prompt.addEventListener("input", () => {
    autoGrow();
    syncSendEnabled();
  });
  els.prompt.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      els.form.requestSubmit();
    }
  });
  els.btnSettings.addEventListener("click", openSettings);
  els.btnSettingsTop.addEventListener("click", openSettings);
  els.btnSettingsClose.addEventListener("click", closeSettings);
  els.btnSettingsCancel.addEventListener("click", closeSettings);
  els.btnSettingsSave.addEventListener("click", saveSettings);
  els.settingsBackdrop.addEventListener("click", (e) => {
    if (e.target === els.settingsBackdrop) closeSettings();
  });
  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape" && !els.settingsBackdrop.hidden) closeSettings();
  });

  async function loadEnvironment() {
    try {
      const env = await api("/api/environment");
      const label = document.getElementById("env-label");
      if (!label) return;
      if (env.is_kali) {
        label.textContent = `${env.shell_label} · Settings`;
      } else if (env.mode === "windows") {
        label.textContent = "Use Kali VM/WSL for Run · Settings";
      } else {
        label.textContent = `${env.shell_label || "Linux"} · Settings`;
      }
    } catch (_err) {
      /* ignore */
    }
  }

  (async function init() {
    try {
      await loadProviders();
      await loadEnvironment();
      await loadConversations();
      if (state.conversations.length) {
        await selectConversation(state.conversations[0].id);
      } else {
        renderThread(null);
      }
      syncSendEnabled();
      els.prompt.focus();
    } catch (err) {
      els.thread.innerHTML = `<div class="empty-state"><h1>Cannot reach API</h1><p style="color:var(--text-secondary)">${err.message}</p></div>`;
    }
  })();
})();
