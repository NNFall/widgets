from __future__ import annotations

import asyncio
import hashlib
import io
import re
from dataclasses import dataclass
from typing import Any, Iterable

from PIL import Image
from playwright.async_api import Browser, BrowserContext, Error as PlaywrightError, Page, async_playwright

from .models import WidgetArtifact
from .preview import build_preview_document
from .visual_models import (
    LayoutEvidence,
    LayoutState,
    RegionEvidence,
    ScreenshotEvidence,
    ScreenshotState,
)


MAX_SCREENSHOT_BYTES = 1_500_000
MAX_INLINE_BYTES = 8_000_000
JPEG_QUALITY = 80
CHANNEL_ID = "browser-audit-channel-20260719"
_LEDGER_QUIESCENCE_MS = 650
_REQUEST_ID_PATTERN = re.compile(
    r"^(?:[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}|request-[0-9a-f]{24})$",
    re.IGNORECASE,
)

_BLOCK_NETWORK_APIS = """(() => {
  const denyConstructor = name => {
    if (!(name in globalThis)) return;
    const blocked = function() {
      console.error(`KAIGO_AUDIT_POLICY_BLOCKED:${name}`);
      throw new DOMException(`${name} is disabled during browser audit`, 'SecurityError');
    };
    Object.defineProperty(blocked, 'name', {value: name});
    Object.defineProperty(globalThis, name, {
      configurable: false,
      enumerable: true,
      writable: false,
      value: blocked
    });
  };
  for (const name of [
    'RTCPeerConnection', 'webkitRTCPeerConnection', 'mozRTCPeerConnection',
    'WebTransport', 'MessageChannel', 'BroadcastChannel'
  ]) denyConstructor(name);

  const pending = {
    timeouts: new Set(),
    intervals: new Set(),
    animationFrames: new Set(),
    idleCallbacks: new Set(),
    abortSignals: new Set(),
    scheduledTasks: new Set()
  };
  let sealed = false;
  let violations = 0;
  const clickTargets = new WeakSet();
  const keyboardTargets = new WeakSet();
  const windowMessageListeners = new Set();
  const replaceOn = (target, name, value) => Object.defineProperty(target, name, {
    configurable: false,
    enumerable: true,
    writable: false,
    value
  });
  const replace = (name, value) => replaceOn(globalThis, name, value);
  const noteExecution = () => { if (sealed) violations += 1; };
  const noteSchedule = () => { if (sealed) violations += 1; };
  if (globalThis.EventTarget
      && typeof globalThis.EventTarget.prototype.addEventListener === 'function') {
    const nativeAddEventListener = globalThis.EventTarget.prototype.addEventListener;
    const nativeRemoveEventListener = globalThis.EventTarget.prototype.removeEventListener;
    replaceOn(globalThis.EventTarget.prototype, 'addEventListener', function(type, listener, options) {
      const normalizedType = String(type).toLowerCase();
      if (normalizedType === 'click' && globalThis.Element && this instanceof globalThis.Element) {
        clickTargets.add(this);
      }
      if (['keydown', 'keyup', 'keypress'].includes(normalizedType)
          && globalThis.Element && this instanceof globalThis.Element) {
        keyboardTargets.add(this);
      }
      if (normalizedType === 'message' && this === globalThis && listener) {
        if (!windowMessageListeners.has(listener) && windowMessageListeners.size >= 1) {
          violations += 1;
          console.error('KAIGO_AUDIT_POLICY_BLOCKED:additional-window-message-listener');
          throw new DOMException(
            'Only the trusted chat response listener is allowed during browser audit',
            'SecurityError'
          );
        }
        windowMessageListeners.add(listener);
      }
      return Reflect.apply(nativeAddEventListener, this, [type, listener, options]);
    });
    replaceOn(globalThis.EventTarget.prototype, 'removeEventListener', function(type, listener, options) {
      if (String(type).toLowerCase() === 'message' && this === globalThis && listener) {
        windowMessageListeners.delete(listener);
      }
      return Reflect.apply(nativeRemoveEventListener, this, [type, listener, options]);
    });
  }
  replace('__kaigoAuditHasClickListener', node => clickTargets.has(node));
  replace('__kaigoAuditHasKeyboardListener', node => keyboardTargets.has(node));
  const auditActionKey = node => {
    const parts = [];
    for (let current = node; current && current !== document.documentElement;
         current = current.parentElement) {
      const parent = current.parentElement;
      const index = parent ? Array.prototype.indexOf.call(parent.children, current) : 0;
      parts.push(`${current.tagName.toLowerCase()}:${index}`);
    }
    return parts.reverse().join('/');
  };
  const auditGenericActions = () => {
    const ariaSelector = [
      '[role="button"]', '[role="link"]', '[role="checkbox"]',
      '[role="menuitem"]', '[role="menuitemcheckbox"]', '[role="menuitemradio"]',
      '[role="option"]', '[role="radio"]', '[role="switch"]', '[role="tab"]',
      '[role="treeitem"]', '[role="slider"]', '[role="spinbutton"]'
    ].join(',');
    const knownSelector = [
      '[data-region="launcher"]', '[data-action="close"]', '[data-action="send"]',
      '[data-kaigo-runtime-retry="true"]', '[data-suggestion]',
      '[data-region="suggestions"] button'
    ].join(',');
    const visible = node => {
      const rect = node.getBoundingClientRect();
      if (rect.width <= 0 || rect.height <= 0 || node.hidden) return false;
      for (let current = node; current; current = current.parentElement) {
        const style = getComputedStyle(current);
        if (style.display === 'none' || style.visibility === 'hidden'
            || Number(style.opacity) === 0) return false;
      }
      return true;
    };
    return Array.from(document.querySelectorAll('*')).filter(node => {
      if (node.matches(knownSelector) || !visible(node)) return false;
      const nativeButton = node.matches('button,input[type="button" i]');
      const ariaAction = node.matches(ariaSelector);
      const customAction = (node.hasAttribute('onclick') || typeof node.onclick === 'function'
          || clickTargets.has(node))
        && !node.querySelector('button,input,' + ariaSelector);
      if (!(nativeButton || ariaAction || customAction)) return false;
      if (node instanceof HTMLButtonElement) {
        const type = (node.getAttribute('type') || 'submit').toLowerCase();
        const nativeFormBehavior = Boolean(node.form) && ['submit', 'reset'].includes(type);
        const nativeTargetBehavior = node.hasAttribute('popovertarget')
          || node.hasAttribute('commandfor');
        if (nativeFormBehavior || nativeTargetBehavior) return false;
      }
      return true;
    });
  };
  replace('__kaigoAuditActionKey', auditActionKey);
  replace('__kaigoAuditGenericActions', auditGenericActions);
  if ('onmessage' in globalThis) replace('onmessage', null);
  if (globalThis.Element && typeof globalThis.Element.prototype.attachShadow === 'function') {
    replaceOn(globalThis.Element.prototype, 'attachShadow', function() {
      console.error('KAIGO_AUDIT_POLICY_BLOCKED:ShadowRoot');
      throw new DOMException(
        'Shadow roots are disabled during deterministic browser audit',
        'SecurityError'
      );
    });
  }
  const nativeSetTimeout = globalThis.setTimeout.bind(globalThis);
  const nativeClearTimeout = globalThis.clearTimeout.bind(globalThis);
  const nativeSetInterval = globalThis.setInterval.bind(globalThis);
  const nativeClearInterval = globalThis.clearInterval.bind(globalThis);
  replace('setTimeout', (callback, delay, ...args) => {
    let handle;
    const wrapped = (...callbackArgs) => {
      pending.timeouts.delete(handle);
      noteExecution();
      if (typeof callback === 'function') {
        return Reflect.apply(callback, globalThis, callbackArgs);
      }
      return (0, eval)(String(callback));
    };
    noteSchedule();
    handle = nativeSetTimeout(wrapped, delay, ...args);
    pending.timeouts.add(handle);
    return handle;
  });
  replace('clearTimeout', handle => {
    pending.timeouts.delete(handle);
    pending.intervals.delete(handle);
    return nativeClearTimeout(handle);
  });
  replace('setInterval', (callback, delay, ...args) => {
    let handle;
    const wrapped = (...callbackArgs) => {
      noteExecution();
      if (typeof callback === 'function') {
        return Reflect.apply(callback, globalThis, callbackArgs);
      }
      return (0, eval)(String(callback));
    };
    noteSchedule();
    handle = nativeSetInterval(wrapped, delay, ...args);
    pending.intervals.add(handle);
    return handle;
  });
  replace('clearInterval', handle => {
    pending.timeouts.delete(handle);
    pending.intervals.delete(handle);
    return nativeClearInterval(handle);
  });
  if ('requestAnimationFrame' in globalThis) {
    const nativeRequestAnimationFrame = globalThis.requestAnimationFrame.bind(globalThis);
    const nativeCancelAnimationFrame = globalThis.cancelAnimationFrame.bind(globalThis);
    replace('requestAnimationFrame', callback => {
      let handle;
      noteSchedule();
      handle = nativeRequestAnimationFrame(timestamp => {
        pending.animationFrames.delete(handle);
        noteExecution();
        return callback(timestamp);
      });
      pending.animationFrames.add(handle);
      return handle;
    });
    replace('cancelAnimationFrame', handle => {
      pending.animationFrames.delete(handle);
      return nativeCancelAnimationFrame(handle);
    });
  }
  if ('requestIdleCallback' in globalThis) {
    const nativeRequestIdleCallback = globalThis.requestIdleCallback.bind(globalThis);
    const nativeCancelIdleCallback = globalThis.cancelIdleCallback.bind(globalThis);
    replace('requestIdleCallback', (callback, options) => {
      let handle;
      noteSchedule();
      handle = nativeRequestIdleCallback(deadline => {
        pending.idleCallbacks.delete(handle);
        noteExecution();
        return callback(deadline);
      }, options);
      pending.idleCallbacks.add(handle);
      return handle;
    });
    replace('cancelIdleCallback', handle => {
      pending.idleCallbacks.delete(handle);
      return nativeCancelIdleCallback(handle);
    });
  }
  if (globalThis.AbortSignal && typeof globalThis.AbortSignal.timeout === 'function') {
    const nativeAbortTimeout = globalThis.AbortSignal.timeout.bind(globalThis.AbortSignal);
    replaceOn(globalThis.AbortSignal, 'timeout', delay => {
      noteSchedule();
      const signal = nativeAbortTimeout(delay);
      pending.abortSignals.add(signal);
      signal.addEventListener('abort', () => {
        pending.abortSignals.delete(signal);
        noteExecution();
      }, {once: true});
      return signal;
    });
  }
  if (globalThis.scheduler && typeof globalThis.scheduler.postTask === 'function') {
    const nativePostTask = globalThis.scheduler.postTask.bind(globalThis.scheduler);
    replaceOn(globalThis.scheduler, 'postTask', (callback, options) => {
      noteSchedule();
      const token = {};
      pending.scheduledTasks.add(token);
      let promise;
      try {
        promise = nativePostTask((...args) => {
          pending.scheduledTasks.delete(token);
          noteExecution();
          return Reflect.apply(callback, globalThis, args);
        }, options);
      } catch (error) {
        pending.scheduledTasks.delete(token);
        throw error;
      }
      Promise.resolve(promise).then(
        () => pending.scheduledTasks.delete(token),
        () => pending.scheduledTasks.delete(token)
      );
      return promise;
    });
  }
  const snapshot = () => Object.freeze({
    timeouts: pending.timeouts.size,
    intervals: pending.intervals.size,
    animationFrames: pending.animationFrames.size,
    idleCallbacks: pending.idleCallbacks.size,
    abortSignals: pending.abortSignals.size,
    scheduledTasks: pending.scheduledTasks.size,
    violations
  });
  Object.defineProperty(globalThis, '__kaigoAuditPendingAsync', {
    configurable: false,
    enumerable: false,
    get: snapshot
  });
  replace('__kaigoAuditSealAsync', () => {
    sealed = true;
    return snapshot();
  });
})()"""

_SETTLE_DOCUMENT = """async stabilityMs => {
  const styleSignature = () => Array.from(document.querySelectorAll('*')).map(node => {
    const style = getComputedStyle(node);
    const rect = node.getBoundingClientRect();
    return [
      node.tagName, node.className, node.getAttribute('style') || '',
      Math.round(rect.x * 10), Math.round(rect.y * 10),
      Math.round(rect.width * 10), Math.round(rect.height * 10),
      style.display, style.visibility, style.opacity, style.backgroundColor,
      style.color, style.borderColor, style.transform
    ].join('|');
  }).join('\\n');
  const settleAnimations = () => {
    for (const animation of document.getAnimations({subtree: true})) {
      try {
        const timing = animation.effect?.getComputedTiming();
        if (timing && Number.isFinite(timing.endTime)) {
          animation.finish();
          animation.commitStyles();
        }
      } catch (_) {
      } finally {
        try { animation.cancel(); } catch (_) {}
      }
    }
  };
  settleAnimations();
  await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
  settleAnimations();
  const before = styleSignature();
  let mutations = 0;
  const observer = new MutationObserver(records => { mutations += records.length; });
  observer.observe(document.documentElement, {
    attributes: true, childList: true, characterData: true, subtree: true
  });
  await new Promise(resolve => setTimeout(resolve, stabilityMs));
  const after = styleSignature();
  mutations += observer.takeRecords().length;
  observer.disconnect();
  const active = document.getAnimations({subtree: true});
  if (active.length) throw new Error(`active animations after settle: ${active.length}`);
  if (mutations || before !== after) {
    throw new Error(`visual DOM/style changed during settle: ${mutations}`);
  }
}"""

_BEGIN_VISUAL_WATCH = """() => {
  const signature = () => Array.from(document.querySelectorAll('*')).map(node => {
    const style = getComputedStyle(node);
    const rect = node.getBoundingClientRect();
    return [node.tagName, node.className, node.getAttribute('style') || '',
      Math.round(rect.x * 10), Math.round(rect.y * 10),
      Math.round(rect.width * 10), Math.round(rect.height * 10),
      style.display, style.visibility, style.opacity, style.backgroundColor,
      style.color, style.borderColor, style.transform].join('|');
  }).join('\\n');
  const mutations = [];
  const recordMutations = records => records.forEach(record => mutations.push({
    type: record.type,
    attribute: record.attributeName || null,
    target: record.target instanceof Element
      ? `${record.target.tagName.toLowerCase()}#${record.target.id || ''}.${record.target.className || ''}`
      : record.target.nodeName
  }));
  const observer = new MutationObserver(recordMutations);
  observer.observe(document.documentElement, {
    attributes: true, childList: true, characterData: true, subtree: true
  });
  window.__kaigoVisualWatch = {observer, signature, before: signature(), mutations, recordMutations};
}"""

_END_VISUAL_WATCH = """() => {
  const watch = window.__kaigoVisualWatch;
  if (!watch) throw new Error('visual watch was not armed');
  watch.recordMutations(watch.observer.takeRecords());
  const mutations = watch.mutations.length;
  const changed = watch.before !== watch.signature();
  watch.observer.disconnect();
  delete window.__kaigoVisualWatch;
  return {
    mutations,
    mutation_details: watch.mutations.slice(0, 12),
    changed,
    active: document.getAnimations({subtree:true}).length
  };
}"""

_SCREENSHOT_ORDER = tuple(ScreenshotState)
_LAYOUT_ORDER = tuple(LayoutState)
_VIEWPORTS = (
    ("desktop", 1440, 900),
    ("mobile", 390, 844),
)
_NARROW_DESKTOP_VIEWPORT = (601, 700)
_REQUIRED_REGIONS = (
    "root",
    "launcher",
    "panel",
    "header",
    "messages",
    "suggestions",
    "composer",
)
_MIN_INTERACTIVE_TARGET_PX = 43.5


class BrowserAuditError(RuntimeError):
    def __init__(
        self,
        error_code: str,
        message: str,
        *,
        diagnostic: str | None = None,
        failures: tuple[str, ...] = (),
    ):
        super().__init__(message)
        self.error_code = error_code
        self.diagnostic = diagnostic
        self.failures = tuple(str(item) for item in failures if str(item).strip())


@dataclass(frozen=True)
class CapturedScreenshot:
    evidence: ScreenshotEvidence
    data: bytes

    def __post_init__(self) -> None:
        if not isinstance(self.evidence, ScreenshotEvidence):
            raise ValueError("evidence must be ScreenshotEvidence")
        if not isinstance(self.data, bytes):
            raise ValueError("screenshot data must be bytes")
        if len(self.data) != self.evidence.byte_count:
            raise ValueError("screenshot byte count does not match evidence")
        if len(self.data) > MAX_SCREENSHOT_BYTES:
            raise ValueError("screenshot exceeds inline byte limit")
        if hashlib.sha256(self.data).hexdigest() != self.evidence.sha256:
            raise ValueError("screenshot digest does not match evidence")
        if not self.data.startswith(b"\xff\xd8\xff") or not self.data.endswith(b"\xff\xd9"):
            raise ValueError("screenshot is not a complete JPEG")
        try:
            with Image.open(io.BytesIO(self.data)) as image:
                image.verify()
            with Image.open(io.BytesIO(self.data)) as image:
                if image.format != "JPEG" or image.size != (
                    self.evidence.width,
                    self.evidence.height,
                ):
                    raise ValueError("screenshot dimensions or format do not match evidence")
        except (OSError, SyntaxError) as exc:
            raise ValueError("screenshot cannot be decoded") from exc


@dataclass(frozen=True)
class BrowserAuditReport:
    screenshots: tuple[CapturedScreenshot, ...]
    layouts: tuple[LayoutEvidence, ...]

    def __post_init__(self) -> None:
        screenshots = tuple(self.screenshots)
        layouts = tuple(self.layouts)
        if any(not isinstance(item, CapturedScreenshot) for item in screenshots):
            raise ValueError("screenshots must contain CapturedScreenshot values")
        if any(not isinstance(item, LayoutEvidence) for item in layouts):
            raise ValueError("layouts must contain LayoutEvidence values")
        states = tuple(item.evidence.state for item in screenshots)
        if states != _SCREENSHOT_ORDER:
            raise ValueError("browser audit requires exactly six ordered screenshot states")
        layout_states = tuple(item.state for item in layouts)
        if layout_states != _LAYOUT_ORDER:
            raise ValueError("browser audit requires exactly eight ordered layout states")
        ids = tuple(item.evidence.screenshot_id for item in screenshots)
        if len(ids) != len(set(ids)):
            raise ValueError("browser audit screenshot ids must be unique")
        for screenshot in screenshots:
            evidence = screenshot.evidence
            if evidence.screenshot_id != evidence.state.value:
                raise ValueError("browser audit screenshot ids must equal their state ids")
            expected_size = (
                (1440, 900)
                if evidence.state.value.startswith("desktop")
                else (390, 844)
            )
            if evidence.mime_type != "image/jpeg" or (
                evidence.width,
                evidence.height,
            ) != expected_size:
                raise ValueError("browser audit screenshot format or viewport is invalid")
        shot_by_state = {item.evidence.state.value: item.evidence for item in screenshots}
        for layout in layouts:
            expected_size = (
                (1440, 900)
                if layout.state.value.startswith("desktop")
                else (390, 844)
            )
            if (layout.viewport_width, layout.viewport_height) != expected_size:
                raise ValueError("browser audit layout viewport is invalid")
            if layout.state.value.endswith("after_turn_1"):
                if layout.screenshot_id is not None:
                    raise ValueError("after_turn_1 must remain layout-only")
                continue
            expected = shot_by_state[layout.state.value].screenshot_id
            if layout.screenshot_id != expected:
                raise ValueError("layout screenshot relation is invalid")
        if sum(len(item.data) for item in screenshots) > MAX_INLINE_BYTES:
            raise ValueError("browser audit exceeds aggregate inline byte limit")
        object.__setattr__(self, "screenshots", screenshots)
        object.__setattr__(self, "layouts", layouts)

    @property
    def total_bytes(self) -> int:
        return sum(len(item.data) for item in self.screenshots)

    def screenshot(self, state: ScreenshotState | str) -> CapturedScreenshot:
        key = ScreenshotState(state)
        return next(item for item in self.screenshots if item.evidence.state is key)


@dataclass
class _RuntimeFailures:
    console: list[str]
    page: list[str]
    requests: list[str]
    policy: list[str]


@dataclass
class _ActionProbeSession:
    context: BrowserContext | None = None
    failures: _RuntimeFailures | None = None


def _failure_text(value: Any, *, limit: int = 500) -> str:
    text = str(value).strip().replace("\x00", "")
    return (text or "unknown failure")[:limit]


def _region_name(selector: str, index: int | None = None) -> str:
    base = selector.removeprefix("[data-region=\"").removesuffix("\"]")
    return base if index is None else f"action.{base}.{index}"


def _parent_fixture() -> str:
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><style>
html,body,#preview{{margin:0;width:100%;height:100%;border:0;background:#fff;overflow:hidden}}
</style></head><body><iframe id="preview" title="audit" sandbox="allow-scripts"></iframe></body></html>"""


class BrowserAudit:
    _cleanup_orphans: set[asyncio.Future[Any]] = set()
    def __init__(
        self,
        *,
        browser: Browser | None = None,
        timeout_ms: int = 10_000,
        total_timeout_seconds: float = 120,
    ) -> None:
        self._browser = browser
        self._timeout_ms = timeout_ms
        if total_timeout_seconds <= 0 or total_timeout_seconds > 300:
            raise ValueError("total_timeout_seconds is invalid")
        self._total_timeout_seconds = total_timeout_seconds

    async def audit(self, artifact: WidgetArtifact) -> BrowserAuditReport:
        if not isinstance(artifact, WidgetArtifact):
            raise TypeError("artifact must be WidgetArtifact")
        playwright = None
        browser = self._browser
        probe_session = _ActionProbeSession()
        deadline = asyncio.get_running_loop().time() + self._total_timeout_seconds
        try:
            async with asyncio.timeout(self._total_timeout_seconds):
                try:
                    operation_timeout = max(
                        0.001,
                        self._total_timeout_seconds
                        - min(5.0, self._total_timeout_seconds / 2),
                    )
                    async with asyncio.timeout(operation_timeout):
                        if browser is None:
                            playwright = await async_playwright().start()
                            browser = await playwright.chromium.launch(
                                headless=True,
                                args=["--force-webrtc-ip-handling-policy=disable_non_proxied_udp"],
                            )
                        screenshots: list[CapturedScreenshot] = []
                        layouts: list[LayoutEvidence] = []
                        action_results: dict[tuple[str, str], str] = {}
                        for prefix, width, height in _VIEWPORTS:
                            viewport_shots, viewport_layouts = await self._audit_viewport(
                                browser=browser,
                                artifact=artifact,
                                prefix=prefix,
                                width=width,
                                height=height,
                                deadline=deadline,
                                action_results=action_results,
                                probe_session=probe_session,
                            )
                            screenshots.extend(viewport_shots)
                            layouts.extend(viewport_layouts)
                        await self._assert_narrow_desktop_geometry(
                            browser=browser,
                            artifact=artifact,
                            deadline=deadline,
                        )
                        report = BrowserAuditReport(
                            screenshots=tuple(screenshots), layouts=tuple(layouts)
                        )
                        self._assert_release_gate(report)
                        await self._retry_probe(
                            browser=browser, artifact=artifact, deadline=deadline
                        )
                        return report
                finally:
                    try:
                        if probe_session.context is not None:
                            await self._bounded_cleanup(
                                probe_session.context.close(),
                                self._cleanup_timeout(deadline),
                            )
                            probe_session.context = None
                            probe_session.failures = None
                    finally:
                        if self._browser is None and browser is not None:
                            try:
                                await self._bounded_cleanup(
                                    browser.close(), self._cleanup_timeout(deadline)
                                )
                            except BaseException as browser_close_error:
                                if playwright is not None:
                                    try:
                                        await self._bounded_cleanup(
                                            playwright.stop(), self._cleanup_timeout(deadline)
                                        )
                                    except BaseException:
                                        if isinstance(
                                            browser_close_error, asyncio.CancelledError
                                        ):
                                            raise browser_close_error
                                        raise
                                raise
                            else:
                                if playwright is not None:
                                    await self._bounded_cleanup(
                                        playwright.stop(), self._cleanup_timeout(deadline)
                                    )
                        elif playwright is not None:
                            await self._bounded_cleanup(
                                playwright.stop(), self._cleanup_timeout(deadline)
                            )
        except BrowserAuditError:
            raise
        except (ValueError, asyncio.TimeoutError, PlaywrightError) as exc:
            raise BrowserAuditError(
                "browser_gate_failed",
                "Виджет не прошёл детерминированную браузерную проверку",
                diagnostic=f"{type(exc).__name__}: {exc}",
            ) from exc

    @classmethod
    async def _bounded_cleanup(cls, awaitable, timeout_seconds: float = 5) -> None:
        cls._cleanup_orphans = {
            item for item in cls._cleanup_orphans if not item.done()
        }
        if cls._cleanup_orphans:
            close = getattr(awaitable, "close", None)
            if callable(close):
                close()
            raise RuntimeError("a previous cleanup coroutine is still alive")
        task = asyncio.ensure_future(awaitable)

        def consume_result(done: asyncio.Future) -> None:
            try:
                done.exception()
            except BaseException:
                pass
            cls._cleanup_orphans.discard(done)

        async def cancel_repeatedly() -> bool:
            for _ in range(4):
                if task.done():
                    return True
                task.cancel()
                await asyncio.sleep(0)
            if task.done():
                return True
            get_coro = getattr(task, "get_coro", None)
            if callable(get_coro):
                coroutine = get_coro()
                close = getattr(coroutine, "close", None)
                if callable(close):
                    try:
                        close()
                    except BaseException:
                        pass
            task.cancel()
            done, _pending = await asyncio.wait({task}, timeout=0.05)
            return bool(done)

        try:
            await asyncio.wait_for(asyncio.shield(task), timeout=timeout_seconds)
        except asyncio.CancelledError:
            task.add_done_callback(consume_result)
            if not await cancel_repeatedly():
                cls._cleanup_orphans.add(task)
            raise
        except asyncio.TimeoutError:
            task.add_done_callback(consume_result)
            if not await cancel_repeatedly():
                cls._cleanup_orphans.add(task)
                raise RuntimeError("cleanup coroutine resisted bounded cancellation")

    @staticmethod
    def _cleanup_timeout(deadline: float | None) -> float:
        if deadline is None:
            return 5
        remaining = deadline - asyncio.get_running_loop().time()
        return max(0.001, min(5, remaining))

    @staticmethod
    async def _settle_frame(frame) -> None:
        await frame.evaluate(_SETTLE_DOCUMENT, 160)

    def _configure_page(self, page: Page, failures: _RuntimeFailures) -> None:
        page.set_default_timeout(self._timeout_ms)
        page.on(
            "console",
            lambda message: failures.console.append(_failure_text(message.text))
            if message.type == "error"
            else None,
        )
        page.on("pageerror", lambda error: failures.page.append(_failure_text(error)))
        page.on(
            "requestfailed",
            lambda request: failures.requests.append(_failure_text(request.url)),
        )
        page.on(
            "download",
            lambda download: failures.policy.append(
                "download:" + _failure_text(download.suggested_filename)
            ),
        )
        page.on(
            "worker",
            lambda worker: failures.policy.append("worker:" + _failure_text(worker.url)),
        )
        page.on(
            "framenavigated",
            lambda frame: failures.policy.append("navigation:" + _failure_text(frame.url))
            if frame.url not in {"about:blank", "about:srcdoc"}
            else None,
        )

    async def _new_probe_page(
        self, context: BrowserContext, failures: _RuntimeFailures
    ) -> Page:
        page = await context.new_page()
        self._configure_page(page, failures)
        return page

    async def _new_context(
        self,
        browser: Browser,
        width: int,
        height: int,
        *,
        deadline: float | None = None,
    ) -> tuple[BrowserContext, Page, _RuntimeFailures]:
        failures = _RuntimeFailures(console=[], page=[], requests=[], policy=[])
        is_mobile = width == 390
        context = await browser.new_context(
            viewport={"width": width, "height": height},
            device_scale_factor=1,
            service_workers="block",
            accept_downloads=False,
            java_script_enabled=True,
            is_mobile=is_mobile,
            has_touch=is_mobile,
            reduced_motion="reduce",
        )
        try:
            await context.add_init_script(_BLOCK_NETWORK_APIS)
            async def block_request(route, request):
                failures.policy.append(_failure_text(request.url))
                await route.abort("blockedbyclient")

            await context.route("**/*", block_request)
            await context.route_web_socket(
                "**/*",
                lambda websocket: (
                    failures.policy.append("websocket:" + _failure_text(websocket.url)),
                    websocket.close(code=1008, reason="blocked"),
                )[1],
            )
            primary: list[Page] = []

            async def close_popup(candidate: Page) -> None:
                if not primary or primary[0].is_closed():
                    primary[:] = [candidate]
                    return
                if candidate is not primary[0]:
                    failures.policy.append("popup:" + _failure_text(candidate.url))
                    await candidate.close()

            context.on("page", close_popup)
            page = await context.new_page()
            self._configure_page(page, failures)
            return context, page, failures
        except BaseException:
            await self._bounded_cleanup(
                context.close(), self._cleanup_timeout(deadline)
            )
            raise

    async def _mount(self, page: Page, artifact: WidgetArtifact, *, retry_mode: bool = False):
        document = build_preview_document(artifact, channel_id=CHANNEL_ID)
        if re.search(r"<template\b[^>]*\bshadowroot(?:mode)?\s*=", document, re.IGNORECASE):
            raise ValueError("declarative shadow roots are disabled during browser audit")
        await page.set_content(_parent_fixture(), wait_until="load")
        await page.evaluate(
            """({previewDocument, retryMode}) => {
              const frame = document.getElementById('preview');
              const attempts = new Map();
              window.__auditEvents = [];
              window.__auditReplies = [];
              window.__auditLedgerSealed = false;
              window.__auditLedgerViolation = false;
              window.__auditReleaseResponse = () => {
                const reply = window.__auditReplies.shift();
                if (!reply) throw new Error('no pending audit response');
                reply();
              };
              window.addEventListener('message', event => {
                const data = event.data;
                if (event.source !== frame.contentWindow || !data || data.source !== 'kaigo-builder-preview' || data.version !== 2) return;
                window.__auditEvents.push(data);
                if (data.type !== 'chat.request') return;
                if (window.__auditLedgerSealed) window.__auditLedgerViolation = true;
                const count = (attempts.get(data.request_id) || 0) + 1;
                attempts.set(data.request_id, count);
                const base = {source:'kaigo-builder-parent',version:2,channel_id:data.channel_id,revision:data.revision,request_id:data.request_id};
                if (retryMode && count === 1) {
                  window.__auditReplies.push(() => event.source.postMessage({...base,type:'chat.error',message:'Временная ошибка',retryable:true}, '*'));
                  return;
                }
                const turn = window.__auditEvents.filter(item => item.type === 'chat.request').length;
                const answer = turn === 1 ? 'RAW BUREAU помогает упаковать цифровой продукт и запустить его.' : 'Оставьте задачу и контакт — команда предложит следующий шаг.';
                window.__auditReplies.push(() => event.source.postMessage({...base,type:'chat.response',text:answer}, '*'));
              });
              frame.srcdoc = previewDocument;
            }""",
            {"previewDocument": document, "retryMode": retry_mode},
        )
        await page.wait_for_function(
            "window.__auditEvents && window.__auditEvents.some(item => item.type === 'rendered')"
        )
        child_frame = page.frames[-1]
        await child_frame.evaluate(
            """() => {
              const originalAnimate = Element.prototype.animate;
              Object.defineProperty(Element.prototype, 'animate', {
                configurable: true,
                writable: true,
                value: function(...args) {
                  const animation = originalAnimate.apply(this, args);
                  try { animation.cancel(); } catch (_) {}
                  return animation;
                }
              });
            }"""
        )
        await child_frame.add_style_tag(
            content=(
                "*,*::before,*::after{transition:none!important;transition-duration:0s!important;"
                "scroll-behavior:auto!important;caret-color:transparent!important}"
            )
        )
        await self._settle_frame(child_frame)
        return page.frame_locator("#preview")

    async def _assert_narrow_desktop_geometry(
        self,
        *,
        browser: Browser,
        artifact: WidgetArtifact,
        deadline: float | None = None,
    ) -> None:
        width, height = _NARROW_DESKTOP_VIEWPORT
        context, page, failures = await self._new_context(
            browser, width, height, deadline=deadline
        )
        try:
            frame = await self._mount(page, artifact)
            root = frame.locator('[data-region="root"]')
            launcher = frame.locator('[data-region="launcher"]')
            panel = frame.locator('[data-region="panel"]')
            await root.wait_for(state="attached")
            await launcher.click()
            await page.wait_for_timeout(50)
            if await root.get_attribute("data-state") != "open":
                raise ValueError("narrow desktop launcher did not open the widget")
            box = await panel.bounding_box()
            if box is None:
                raise ValueError("narrow desktop panel is not visible")
            actual_right = width - (box["x"] + box["width"])
            actual_bottom = height - (box["y"] + box["height"])
            geometry_failures = []
            if abs(box["width"] - 372) > 1:
                geometry_failures.append(
                    "narrow desktop panel width must be 372px; "
                    f"actual {box['width']:.1f}px; mobile full-width panel rules "
                    "must apply only at viewport widths <= 600px"
                )
            if abs(actual_right - 20) > 1 or abs(actual_bottom - 20) > 1:
                geometry_failures.append(
                    "narrow desktop panel must keep 20px right/bottom margins; "
                    f"actual right {actual_right:.1f}px, bottom {actual_bottom:.1f}px"
                )
            if box["height"] > min(536, height * 0.68) + 1:
                geometry_failures.append(
                    "narrow desktop panel exceeds height cap; "
                    f"actual {box['height']:.1f}px"
                )
            self._raise_policy_failures(failures)
            if geometry_failures:
                raise BrowserAuditError(
                    "browser_gate_failed",
                    "Widget failed narrow desktop geometry: "
                    + "; ".join(geometry_failures),
                    diagnostic="\n".join(geometry_failures),
                    failures=tuple(geometry_failures),
                )
        finally:
            await self._bounded_cleanup(
                context.close(), self._cleanup_timeout(deadline)
            )

    @staticmethod
    async def _assert_pending_turn(frame, expected_roles: tuple[str, ...]) -> None:
        payload = await frame.locator("body").evaluate(
            """() => {
              const composer = document.querySelector('[data-region="composer"]');
              const send = document.querySelector('[data-action="send"], [data-region="composer"] button');
              return {
                roles: Array.from(document.querySelectorAll('[data-kaigo-runtime-message]')).map(
                  node => node.dataset.kaigoRuntimeMessage
                ),
                busy: composer?.getAttribute('aria-busy') ?? 'missing',
                sendDisabled: Boolean(send?.disabled)
              };
            }"""
        )
        if (
            payload["roles"] != list(expected_roles)
            or payload["busy"] != "true"
            or payload["sendDisabled"] is not True
        ):
            raise ValueError(
                f"pending/retry turn contract is invalid: {payload}"
            )

    @staticmethod
    async def _assert_completed_turn(frame, expected_roles: tuple[str, ...]) -> None:
        payload = await frame.locator("body").evaluate(
            """() => {
              const composer = document.querySelector('[data-region="composer"]');
              const send = document.querySelector('[data-action="send"], [data-region="composer"] button');
              return {
                roles: Array.from(document.querySelectorAll('[data-kaigo-runtime-message]')).map(
                  node => node.dataset.kaigoRuntimeMessage
                ),
                busy: composer?.getAttribute('aria-busy') ?? 'missing',
                sendDisabled: Boolean(send?.disabled),
                statusCount: document.querySelectorAll('[data-kaigo-runtime-status]').length
              };
            }"""
        )
        if (
            payload["roles"] != list(expected_roles)
            or payload["busy"] != "false"
            or payload["sendDisabled"] is not False
            or payload["statusCount"] != 0
        ):
            raise ValueError(
                f"completed turn contract is invalid: {payload}"
            )

    @staticmethod
    async def _transcript(frame) -> list[dict[str, str]]:
        return await frame.locator("[data-kaigo-runtime-message]").evaluate_all(
            """nodes => nodes.map(node => ({
              role: node.dataset.kaigoRuntimeMessage || '',
              text: node.querySelector('[data-kaigo-runtime-content]')?.textContent || ''
            }))"""
        )

    @staticmethod
    async def _assert_chat_request(
        page: Page,
        *,
        expected_count: int,
        expected_text: str,
        expected_revision: int,
        expected_request_id: str | None = None,
        forbidden_request_ids: frozenset[str] = frozenset(),
    ) -> str:
        await page.wait_for_function(
            "count => window.__auditEvents.filter(item => item.type === 'chat.request').length >= count",
            arg=expected_count,
        )
        await page.evaluate(
            "() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))"
        )
        requests = await page.evaluate(
            "() => window.__auditEvents.filter(item => item.type === 'chat.request')"
        )
        if len(requests) != expected_count:
            raise ValueError(
                f"chat.request ledger count is invalid: expected {expected_count}, got {len(requests)}"
            )
        event = requests[-1]
        required_keys = {
            "source", "version", "channel_id", "type", "request_id", "revision", "text"
        }
        if set(event) != required_keys:
            raise ValueError(f"chat.request ledger fields are invalid: {sorted(event)}")
        request_id = event.get("request_id")
        expected_fields = {
            "source": "kaigo-builder-preview",
            "version": 2,
            "channel_id": CHANNEL_ID,
            "type": "chat.request",
            "revision": expected_revision,
            "text": expected_text,
        }
        if any(event.get(key) != value for key, value in expected_fields.items()):
            raise ValueError(f"chat.request ledger payload is invalid: {event}")
        if not isinstance(request_id, str) or not _REQUEST_ID_PATTERN.fullmatch(request_id):
            raise ValueError("chat.request ledger request_id is invalid")
        if expected_request_id is not None and request_id != expected_request_id:
            raise ValueError("retry chat.request did not preserve request_id")
        if expected_request_id is None and request_id in forbidden_request_ids:
            raise ValueError("new chat.request reused an earlier request_id")
        return request_id

    @staticmethod
    async def _assert_final_chat_ledger(page: Page, *, expected_count: int) -> None:
        await page.evaluate("() => { window.__auditLedgerSealed = true; }")
        await page.wait_for_timeout(_LEDGER_QUIESCENCE_MS)
        await page.evaluate(
            "() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))"
        )
        pending_by_frame = []
        for index, frame in enumerate(page.frames[1:], start=1):
            try:
                pending = await frame.evaluate(
                    """() => globalThis.__kaigoAuditSealAsync?.()
                      || globalThis.__kaigoAuditPendingAsync || null"""
                )
            except PlaywrightError as exc:
                raise ValueError(
                    f"untrusted frame {index} detached during final async-task gate"
                ) from exc
            if not isinstance(pending, dict) or set(pending) != {
                "timeouts", "intervals", "animationFrames", "idleCallbacks",
                "abortSignals", "scheduledTasks", "violations"
            }:
                raise ValueError(
                    f"untrusted frame {index} lacks the pending async-task ledger"
                )
            if any(not isinstance(value, int) or value < 0 for value in pending.values()):
                raise ValueError(
                    f"untrusted frame {index} returned an invalid async-task ledger: {pending}"
                )
            if any(pending.values()):
                pending_by_frame.append({"frame": index, **pending})
        if pending_by_frame:
            raise ValueError(
                "untrusted frame has pending async tasks that could emit another "
                f"chat.request after final ledger seal: {pending_by_frame}"
            )
        await page.evaluate(
            "() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)))"
        )
        payload = await page.evaluate(
            """() => ({
              requestCount: window.__auditEvents.filter(item => item.type === 'chat.request').length,
              pendingReplies: window.__auditReplies.length,
              violation: window.__auditLedgerViolation
            })"""
        )
        if payload != {
            "requestCount": expected_count,
            "pendingReplies": 0,
            "violation": False,
        }:
            raise ValueError(f"final chat.request ledger is invalid: {payload}")

    @staticmethod
    async def _release_audit_response(page: Page) -> None:
        await page.evaluate("window.__auditReleaseResponse()")

    async def _audit_viewport(
        self,
        *,
        browser: Browser,
        artifact: WidgetArtifact,
        prefix: str,
        width: int,
        height: int,
        deadline: float | None = None,
        action_results: dict[tuple[str, str], str] | None = None,
        probe_session: _ActionProbeSession | None = None,
    ) -> tuple[list[CapturedScreenshot], list[LayoutEvidence]]:
        owns_probe_session = probe_session is None
        if probe_session is None:
            probe_session = _ActionProbeSession()
        context, page, failures = await self._new_context(
            browser, width, height, deadline=deadline
        )
        try:
            frame = await self._mount(page, artifact)
            root = frame.locator('[data-region="root"]')
            launcher = frame.locator('[data-region="launcher"]')
            await root.wait_for(state="attached")
            if await root.get_attribute("data-state") != "closed":
                raise ValueError("widget must start closed")

            shots: list[CapturedScreenshot] = []
            layouts: list[LayoutEvidence] = []
            closed_state = ScreenshotState(f"{prefix}.closed")
            shots.append(await self._capture(page, closed_state))
            layouts.append(
                await self._measure(
                    frame=frame,
                    state=LayoutState(f"{prefix}.closed"),
                    screenshot_id=closed_state.value,
                    width=width,
                    height=height,
                    failures=failures,
                )
            )

            await self._assert_launcher_pointer_interactable(
                launcher, prefix=prefix
            )

            await self._assert_launcher_keyboard(
                frame,
                root,
                launcher,
                prefix=prefix,
                width=width,
                height=height,
            )
            await launcher.click()
            await page.wait_for_timeout(50)
            if await root.get_attribute("data-state") != "open":
                raise ValueError("launcher did not open the widget")
            generic_action_keys = tuple(
                await frame.locator("body").evaluate(
                    "body => globalThis.__kaigoAuditGenericActions().map("
                    "node => globalThis.__kaigoAuditActionKey(node))"
                )
            )
            if action_results is None:
                action_results = {}
            unseen_action_keys = tuple(
                key
                for key in generic_action_keys
                if (prefix, key) not in action_results
            )
            approved, inert = await self._probe_generic_actions(
                browser=browser,
                artifact=artifact,
                width=width,
                height=height,
                action_keys=unseen_action_keys,
                deadline=deadline,
                probe_session=probe_session,
            )
            action_results.update(
                {(prefix, key): "approved" for key in approved}
            )
            action_results.update({(prefix, key): "inert" for key in inert})
            inert_visible = [
                key
                for key in generic_action_keys
                if action_results.get((prefix, key)) == "inert"
            ]
            if inert_visible:
                raise ValueError(
                    "ARIA action or generic native/custom action lacks observable "
                    "pointer/keyboard activation behavior: "
                    + ", ".join(inert_visible[:4])
                )
            approved_action_keys = frozenset(
                key
                for key in generic_action_keys
                if action_results.get((prefix, key)) == "approved"
            )
            input_box = frame.locator('[data-kaigo-runtime-input="true"]')
            open_state = ScreenshotState(f"{prefix}.open_initial")
            shots.append(await self._capture(page, open_state))
            layouts.append(
                await self._measure(
                    frame=frame,
                    state=LayoutState(f"{prefix}.open_initial"),
                    screenshot_id=open_state.value,
                    width=width,
                    height=height,
                    failures=failures,
                    approved_action_keys=approved_action_keys,
                )
            )
            await self._assert_visible_suggestions_actionable(frame)

            await input_box.fill("Какие задачи решает RAW BUREAU?")
            await input_box.press("Enter")
            await self._assert_pending_turn(frame, ("user",))
            first_request_id = await self._assert_chat_request(
                page,
                expected_count=1,
                expected_text="Какие задачи решает RAW BUREAU?",
                expected_revision=artifact.revision,
            )
            await self._release_audit_response(page)
            await frame.locator('[data-kaigo-runtime-message="assistant"]').nth(0).wait_for()
            await self._assert_completed_turn(frame, ("user", "assistant"))
            layouts.append(
                await self._measure(
                    frame=frame,
                    state=LayoutState(f"{prefix}.after_turn_1"),
                    screenshot_id=None,
                    width=width,
                    height=height,
                    failures=failures,
                    approved_action_keys=approved_action_keys,
                )
            )

            await input_box.fill("Как начать проект?")
            await frame.locator(
                '[data-action="send"], [data-region="composer"] button'
            ).click()
            await self._assert_pending_turn(
                frame, ("user", "assistant", "user")
            )
            await self._assert_chat_request(
                page,
                expected_count=2,
                expected_text="Как начать проект?",
                expected_revision=artifact.revision,
                forbidden_request_ids=frozenset({first_request_id}),
            )
            await self._release_audit_response(page)
            await frame.locator('[data-kaigo-runtime-message="assistant"]').nth(1).wait_for()
            await self._assert_completed_turn(
                frame, ("user", "assistant", "user", "assistant")
            )
            expected_history = [
                {"role": "user", "text": "Какие задачи решает RAW BUREAU?"},
                {
                    "role": "assistant",
                    "text": "RAW BUREAU помогает упаковать цифровой продукт и запустить его.",
                },
                {"role": "user", "text": "Как начать проект?"},
                {
                    "role": "assistant",
                    "text": "Оставьте задачу и контакт — команда предложит следующий шаг.",
                },
            ]
            if await self._transcript(frame) != expected_history:
                raise ValueError("two-turn transcript content is invalid")
            await input_box.focus()
            messages = frame.locator('[data-region="messages"]')
            scroll_metrics = await messages.evaluate(
                """node => ({
                  max: Math.max(0, node.scrollHeight - node.clientHeight),
                  overflowY: getComputedStyle(node).overflowY
                })"""
            )
            if scroll_metrics["overflowY"] in {"hidden", "clip"}:
                raise ValueError("two-turn history is not wheel-accessible")
            if scroll_metrics["max"] > 1:
                await messages.hover()
                before_scroll = await messages.evaluate(
                    "(node, max) => (node.scrollTop = Math.max(1, Math.floor(max * 0.75)))",
                    scroll_metrics["max"],
                )
                await page.mouse.wheel(0, -min(120, max(2, before_scroll)))
                await page.wait_for_timeout(50)
                after_scroll = await messages.evaluate("node => node.scrollTop")
                if after_scroll >= before_scroll - 1:
                    raise ValueError("real wheel did not move the two-turn transcript")
            after_state = ScreenshotState(f"{prefix}.after_turn_2")
            shots.append(await self._capture(page, after_state))
            layouts.append(
                await self._measure(
                    frame=frame,
                    state=LayoutState(f"{prefix}.after_turn_2"),
                    screenshot_id=after_state.value,
                    width=width,
                    height=height,
                    failures=failures,
                    approved_action_keys=approved_action_keys,
                )
            )

            await input_box.fill("Черновик сохраняется")
            await input_box.press("Escape")
            if await root.get_attribute("data-state") != "closed":
                raise ValueError("Escape did not close the widget")
            if not await launcher.evaluate("node => document.activeElement === node"):
                raise ValueError("Escape did not restore focus to the launcher")
            await launcher.click()
            if await input_box.input_value() != "Черновик сохраняется":
                raise ValueError("close/reopen did not preserve the composer draft")
            if await self._transcript(frame) != expected_history:
                raise ValueError("close/reopen did not preserve two-turn history")
            close_control = frame.locator(
                '[data-action="close"], [aria-label*="Закрыть" i]'
            ).first
            await self._assert_control_in_viewport(
                close_control,
                width=width,
                height=height,
                state=f"{prefix}.after_reopen",
                name="close",
            )
            await close_control.click()
            if await root.get_attribute("data-state") != "closed":
                raise ValueError("close button did not close the widget")
            if not await launcher.evaluate("node => document.activeElement === node"):
                raise ValueError("close button did not restore focus to the launcher")
            await launcher.click()
            if await input_box.input_value() != "Черновик сохраняется":
                raise ValueError("button close/reopen did not preserve the composer draft")
            if await self._transcript(frame) != expected_history:
                raise ValueError("button close/reopen did not preserve two-turn history")
            await self._assert_final_chat_ledger(page, expected_count=2)
            self._raise_policy_failures(failures)
            return shots, layouts
        except PlaywrightError:
            self._raise_policy_failures(failures)
            raise
        finally:
            try:
                await self._bounded_cleanup(
                    context.close(), self._cleanup_timeout(deadline)
                )
            finally:
                if owns_probe_session and probe_session.context is not None:
                    await self._bounded_cleanup(
                        probe_session.context.close(), self._cleanup_timeout(deadline)
                    )
                    probe_session.context = None
                    probe_session.failures = None

    @staticmethod
    async def _assert_control_in_viewport(
        control, *, width: int, height: int, state: str, name: str
    ) -> None:
        box = await control.bounding_box()
        if (
            box is not None
            and box["x"] + box["width"] > 0
            and box["y"] + box["height"] > 0
            and box["x"] < width
            and box["y"] < height
        ):
            return
        actual = (
            "missing"
            if box is None
            else (
                f"x={box['x']:.1f}, y={box['y']:.1f}, "
                f"width={box['width']:.1f}, height={box['height']:.1f}"
            )
        )
        failure = (
            f"{state}: {name} control is outside the viewport; actual {actual}; "
            "position launcher and panel themselves as fixed relative to the viewport "
            "and do not add viewport offsets to the root"
        )
        raise BrowserAuditError(
            "browser_gate_failed",
            "Виджет не прошёл детерминированную браузерную проверку: " + failure,
            diagnostic=failure,
            failures=(failure,),
        )

    @classmethod
    async def _assert_launcher_keyboard(
        cls, frame, root, launcher, *, prefix: str, width: int, height: int
    ) -> None:
        semantic = await launcher.evaluate(
            "node => node.tagName === 'BUTTON' || node.getAttribute('role') === 'button'"
        )
        if not semantic:
            raise ValueError("launcher keyboard semantics are invalid")
        close_button = frame.locator('[data-action="close"]')
        for key in ("Enter", "Space"):
            await launcher.focus()
            await launcher.press(key)
            if await root.get_attribute("data-state") != "open":
                raise ValueError(f"launcher keyboard activation failed for {key}")
            await cls._assert_control_in_viewport(
                close_button,
                width=width,
                height=height,
                state=f"{prefix}.keyboard_{key.casefold()}",
                name="close",
            )
            await close_button.click()
            if await root.get_attribute("data-state") != "closed":
                raise ValueError(f"launcher keyboard close cycle failed for {key}")

    @staticmethod
    async def _assert_launcher_pointer_interactable(launcher, *, prefix: str) -> None:
        try:
            await launcher.click(trial=True, timeout=750)
        except PlaywrightError as exc:
            failure = (
                f"{prefix}.closed: launcher pointer is blocked by the hidden panel; "
                "use pointer-events:none on the closed panel and restore "
                "pointer-events:auto only in the open state"
            )
            raise BrowserAuditError(
                "browser_gate_failed",
                "Виджет не прошёл детерминированную браузерную проверку: "
                + failure,
                diagnostic=(
                    failure
                    + "; playwright="
                    + _failure_text(exc, limit=2_000)
                ),
                failures=(failure,),
            ) from exc

    @staticmethod
    async def _assert_visible_suggestions_actionable(frame) -> None:
        suggestions = frame.locator(
            '[data-suggestion], [data-region="suggestions"] button'
        )
        for index in range(await suggestions.count()):
            suggestion = suggestions.nth(index)
            if not await suggestion.is_visible():
                continue
            if await suggestion.is_disabled():
                raise ValueError("visible suggestion is disabled")
            try:
                await suggestion.click(trial=True, timeout=750)
            except PlaywrightError as exc:
                raise ValueError(
                    "visible suggestion is not pointer-interactable"
                ) from exc

    async def _mount_action_probe(
        self,
        page: Page,
        artifact: WidgetArtifact,
        action_key: str,
    ):
        frame = await self._mount(page, artifact)
        await frame.locator('[data-region="launcher"]').click()
        await page.wait_for_timeout(50)
        found = await frame.locator("body").evaluate(
            """(body, key) => {
              const node = globalThis.__kaigoAuditGenericActions().find(
                candidate => globalThis.__kaigoAuditActionKey(candidate) === key
              );
              if (!node) return false;
              node.setAttribute('data-kaigo-audit-action-probe', 'target');
              return true;
            }""",
            action_key,
        )
        if not found:
            raise ValueError(f"generic action changed between isolated mounts: {action_key}")
        return frame, frame.locator('[data-kaigo-audit-action-probe="target"]')

    @staticmethod
    async def _observable_action_state(page: Page, frame) -> dict[str, Any]:
        child = await frame.locator("body").evaluate(
            """body => ({
              html: document.documentElement.outerHTML,
              controls: Array.from(document.querySelectorAll('input,textarea,select,option')).map(
                node => ({
                  key: globalThis.__kaigoAuditActionKey(node),
                  value: 'value' in node ? String(node.value) : null,
                  checked: 'checked' in node ? Boolean(node.checked) : null,
                  selected: 'selected' in node ? Boolean(node.selected) : null,
                  selectedIndex: 'selectedIndex' in node ? Number(node.selectedIndex) : null
                })
              ),
              scroll: Array.from(document.querySelectorAll('*'))
                .filter(node => node.scrollLeft || node.scrollTop)
                .map(node => ({
                  key: globalThis.__kaigoAuditActionKey(node),
                  left: node.scrollLeft,
                  top: node.scrollTop
                })),
              active: document.activeElement
                ? globalThis.__kaigoAuditActionKey(document.activeElement) : null
            })"""
        )
        parent_events = await page.evaluate(
            """() => (window.__auditEvents || []).map(item => ({
              type: item.type,
              request_id: item.request_id || null,
              text: item.text || null
            }))"""
        )
        return {"child": child, "parent_events": parent_events}

    async def _probe_action_activation(
        self,
        *,
        page: Page,
        artifact: WidgetArtifact,
        action_key: str,
        key: str | None,
    ) -> tuple[bool | None, int]:
        frame, action = await self._mount_action_probe(page, artifact, action_key)
        started = asyncio.get_running_loop().time()
        await action.evaluate("node => node.focus({preventScroll:true})")
        if key is not None and not await action.evaluate(
            "node => document.activeElement === node"
        ):
            return None, 1
        before = await self._observable_action_state(page, frame)
        try:
            if key is None:
                await action.click(timeout=750)
            else:
                await action.press(key, timeout=750)
        except PlaywrightError:
            elapsed_ms = max(
                1,
                int((asyncio.get_running_loop().time() - started) * 1_000) + 1,
            )
            return None, elapsed_ms
        await page.wait_for_timeout(60)
        after = await self._observable_action_state(page, frame)
        elapsed_ms = max(
            1,
            int((asyncio.get_running_loop().time() - started) * 1_000) + 1,
        )
        return after != before, elapsed_ms

    async def _probe_action_control(
        self,
        *,
        page: Page,
        artifact: WidgetArtifact,
        action_key: str,
        key: str | None,
        observation_ms: int,
    ) -> bool | None:
        frame, action = await self._mount_action_probe(page, artifact, action_key)
        started = asyncio.get_running_loop().time()
        await action.evaluate("node => node.focus({preventScroll:true})")
        if key is not None and not await action.evaluate(
            "node => document.activeElement === node"
        ):
            return None
        before = await self._observable_action_state(page, frame)
        elapsed_ms = int((asyncio.get_running_loop().time() - started) * 1_000)
        await page.wait_for_timeout(max(0, observation_ms - elapsed_ms))
        after = await self._observable_action_state(page, frame)
        return after != before

    async def _probe_generic_actions(
        self,
        *,
        browser: Browser,
        artifact: WidgetArtifact,
        width: int,
        height: int,
        action_keys: tuple[str, ...],
        deadline: float | None,
        probe_session: _ActionProbeSession,
    ) -> tuple[frozenset[str], frozenset[str]]:
        if not action_keys:
            return frozenset(), frozenset()
        if probe_session.context is None:
            context, initial_page, failures = await self._new_context(
                browser, width, height, deadline=deadline
            )
            probe_session.context = context
            probe_session.failures = failures
        else:
            context = probe_session.context
            failures = probe_session.failures
            if failures is None:
                raise RuntimeError("action probe session failures are missing")
            initial_page = await self._new_probe_page(context, failures)
        first_page: list[Page] = [initial_page]

        async def take_page() -> Page:
            return (
                first_page.pop()
                if first_page
                else await self._new_probe_page(context, failures)
            )

        async def run_activation(
            action_key: str, key: str | None
        ) -> tuple[bool | None, int]:
            page = await take_page()
            try:
                await page.set_viewport_size({"width": width, "height": height})
                return await self._probe_action_activation(
                    page=page,
                    artifact=artifact,
                    action_key=action_key,
                    key=key,
                )
            finally:
                await self._bounded_cleanup(
                    page.close(), self._cleanup_timeout(deadline)
                )

        async def run_control(
            action_key: str, key: str | None, observation_ms: int
        ) -> bool | None:
            page = await take_page()
            try:
                await page.set_viewport_size({"width": width, "height": height})
                return await self._probe_action_control(
                    page=page,
                    artifact=artifact,
                    action_key=action_key,
                    key=key,
                    observation_ms=observation_ms,
                )
            finally:
                await self._bounded_cleanup(
                    page.close(), self._cleanup_timeout(deadline)
                )

        async def run_isolated(action_key: str, key: str | None) -> bool | None:
            activation_effect, observation_ms = await run_activation(action_key, key)
            if not activation_effect:
                return activation_effect
            control_drift = await run_control(action_key, key, observation_ms)
            if control_drift is None:
                return None
            return not control_drift

        try:
            approved: set[str] = set()
            inert: set[str] = set()
            for action_key in action_keys:
                pointer_effect = await run_isolated(action_key, None)
                if pointer_effect is None:
                    continue
                if not pointer_effect:
                    inert.add(action_key)
                    continue
                keyboard_effect = False
                for key in ("Enter", "Space"):
                    if await run_isolated(action_key, key):
                        keyboard_effect = True
                        break
                if keyboard_effect:
                    approved.add(action_key)
                else:
                    inert.add(action_key)
            self._raise_policy_failures(failures)
            return frozenset(approved), frozenset(inert)
        except PlaywrightError:
            self._raise_policy_failures(failures)
            raise
        finally:
            if first_page:
                await self._bounded_cleanup(
                    first_page.pop().close(), self._cleanup_timeout(deadline)
                )

    async def _retry_probe(
        self,
        *,
        browser: Browser,
        artifact: WidgetArtifact,
        deadline: float | None = None,
    ) -> None:
        context, page, failures = await self._new_context(
            browser, 390, 844, deadline=deadline
        )
        try:
            frame = await self._mount(page, artifact, retry_mode=True)
            await frame.locator('[data-region="launcher"]').click()
            input_box = frame.locator('[data-kaigo-runtime-input="true"]')
            await input_box.fill("Проверка повтора")
            await input_box.press("Enter")
            await self._assert_pending_turn(frame, ("user",))
            request_id = await self._assert_chat_request(
                page,
                expected_count=1,
                expected_text="Проверка повтора",
                expected_revision=artifact.revision,
            )
            await self._release_audit_response(page)
            retry = frame.locator('[data-kaigo-runtime-retry="true"]')
            await retry.wait_for()
            child_frames = page.frames[1:]
            if not child_frames:
                raise ValueError("preview iframe is missing during retry audit")
            await self._settle_frame(child_frames[-1])
            error_payload = await frame.locator("body").evaluate(
                """() => ({
                  roles: Array.from(document.querySelectorAll('[data-kaigo-runtime-message]')).map(
                    node => node.dataset.kaigoRuntimeMessage
                  ),
                  userText: Array.from(document.querySelectorAll(
                    '[data-kaigo-runtime-message="user"] [data-kaigo-runtime-content]'
                  )).map(node => node.textContent),
                  status: document.querySelector('[data-kaigo-runtime-status]')?.dataset.kaigoRuntimeStatus,
                  busy: document.querySelector('[data-region="composer"]')?.getAttribute('aria-busy'),
                  sendDisabled: Boolean(document.querySelector(
                    '[data-action="send"], [data-region="composer"] button'
                  )?.disabled)
                })"""
            )
            if error_payload != {
                "roles": ["user"],
                "userText": ["Проверка повтора"],
                "status": "error",
                "busy": "false",
                "sendDisabled": False,
            }:
                raise ValueError(f"retry error contract is invalid: {error_payload}")
            retry_layout = await self._measure(
                frame=frame,
                state=LayoutState.MOBILE_AFTER_TURN_1,
                screenshot_id=None,
                width=390,
                height=844,
                failures=failures,
            )
            regions = {item.region: item for item in retry_layout.regions}
            panel = regions.get("panel")
            retry_action = regions.get("action.retry")
            retry_actions = [
                regions.get(name) for name in ("action.close", "action.send", "action.retry")
            ]
            visible_suggestions = [
                item
                for item in retry_layout.regions
                if item.region.startswith("action.suggestion.") and item.visible
            ]
            interactive_targets = [
                item
                for item in retry_layout.regions
                if item.region.startswith("target.interactive.") and item.visible
            ]
            retry_failures: list[str] = []
            if panel is None or not panel.visible or panel.clipped:
                retry_failures.append("panel is missing, hidden, or clipped")
            for action_name, item in zip(
                ("close", "send", "retry"), retry_actions, strict=True
            ):
                if item is None or not item.visible:
                    retry_failures.append(f"{action_name} action is missing or hidden")
                elif (
                    item.width < _MIN_INTERACTIVE_TARGET_PX
                    or item.height < _MIN_INTERACTIVE_TARGET_PX
                    or item.clipped
                ):
                    retry_failures.append(
                        f"{action_name} action is clipped or undersized at "
                        f"{item.width:.2f}x{item.height:.2f}px"
                    )
            if visible_suggestions:
                retry_failures.append(
                    f"{len(visible_suggestions)} suggestion action(s) remain visible"
                )
            if retry_layout.visible_action_count != 3:
                retry_failures.append(
                    f"visible action count is {retry_layout.visible_action_count}, expected 3"
                )
            if panel is not None:
                outside_targets = [
                    item.region
                    for item in interactive_targets
                    if (
                        item.clipped
                        or item.x < panel.x - 1
                        or item.y < panel.y - 1
                        or item.x + item.width > panel.x + panel.width + 1
                        or item.y + item.height > panel.y + panel.height + 1
                    )
                ]
                if outside_targets:
                    retry_failures.append(
                        "interactive targets outside panel: "
                        + ", ".join(outside_targets[:4])
                    )
            if retry_failures:
                failure = (
                    "mobile.retry_error: retry state must expose exactly close, retry, "
                    "and send as 44px actions inside the panel; "
                    + "; ".join(retry_failures)
                    + '; target .kaigo-widget [data-kaigo-runtime-retry="true"] directly; '
                    + 'do not require .kaigo-widget__message--error or any message-class '
                    + 'ancestor because runtime status does not provide one; hide '
                    + 'suggestions via .kaigo-widget:has([data-kaigo-runtime-status="error"]) '
                    + '[data-region="suggestions"]; do not invent data-action=retry or data-error'
                )
                raise BrowserAuditError(
                    "browser_gate_failed",
                    "Виджет не прошёл детерминированную браузерную проверку: "
                    + failure,
                    diagnostic=failure,
                    failures=(failure,),
                )
            await retry.click()
            await self._assert_pending_turn(frame, ("user",))
            await self._assert_chat_request(
                page,
                expected_count=2,
                expected_text="Проверка повтора",
                expected_revision=artifact.revision,
                expected_request_id=request_id,
            )
            await self._release_audit_response(page)
            await frame.locator('[data-kaigo-runtime-message="assistant"]').wait_for()
            await self._assert_completed_turn(frame, ("user", "assistant"))
            retry_history = [
                {"role": "user", "text": "Проверка повтора"},
                {
                    "role": "assistant",
                    "text": "Оставьте задачу и контакт — команда предложит следующий шаг.",
                },
            ]
            if await self._transcript(frame) != retry_history:
                raise ValueError("retry transcript content is invalid")
            await self._assert_final_chat_ledger(page, expected_count=2)
            self._raise_policy_failures(failures)
        except PlaywrightError:
            self._raise_policy_failures(failures)
            raise
        finally:
            await self._bounded_cleanup(
                context.close(), self._cleanup_timeout(deadline)
            )

    async def _capture(self, page: Page, state: ScreenshotState) -> CapturedScreenshot:
        child_frames = page.frames[1:]
        if not child_frames:
            raise ValueError("preview iframe is missing")
        for frame in child_frames:
            await self._settle_frame(frame)
            await frame.evaluate(_BEGIN_VISUAL_WATCH)
        first = await page.screenshot(
            type="jpeg", quality=JPEG_QUALITY, full_page=False, caret="initial"
        )
        await page.wait_for_timeout(80)
        data = await page.screenshot(
            type="jpeg", quality=JPEG_QUALITY, full_page=False, caret="initial"
        )
        stability = [await frame.evaluate(_END_VISUAL_WATCH) for frame in child_frames]
        active_after_capture = sum(item["active"] for item in stability)
        if (
            active_after_capture
            or any(item["mutations"] or item["changed"] for item in stability)
            or first != data
        ):
            raise ValueError(
                f"{state.value}: visual evidence changed during the paired capture "
                f"(pixels_equal={first == data}, stability={stability})"
            )
        width, height = await page.evaluate("[innerWidth, innerHeight]")
        evidence = ScreenshotEvidence(
            screenshot_id=state.value,
            state=state,
            sha256=hashlib.sha256(data).hexdigest(),
            mime_type="image/jpeg",
            byte_count=len(data),
            width=width,
            height=height,
        )
        return CapturedScreenshot(evidence=evidence, data=data)

    async def _measure(
        self,
        *,
        frame,
        state: LayoutState,
        screenshot_id: str | None,
        width: int,
        height: int,
        failures: _RuntimeFailures,
        approved_action_keys: Iterable[str] = (),
    ) -> LayoutEvidence:
        await frame.locator("html").evaluate(
                """async (root, stabilityMs) => {
              const styleSignature = () => Array.from(root.ownerDocument.querySelectorAll('*')).map(node => {
                const style = getComputedStyle(node);
                const rect = node.getBoundingClientRect();
                return [node.tagName, node.className, node.getAttribute('style') || '',
                  Math.round(rect.x * 10), Math.round(rect.y * 10),
                  Math.round(rect.width * 10), Math.round(rect.height * 10),
                  style.display, style.visibility, style.opacity, style.backgroundColor,
                  style.color, style.borderColor, style.transform].join('|');
              }).join('\\n');
              const cancelAnimations = () => {
                for (const animation of root.ownerDocument.getAnimations({subtree: true})) {
                  try { animation.cancel(); } catch (_) {}
                }
              };
              cancelAnimations();
              await new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
              cancelAnimations();
              const before = styleSignature();
              let mutations = 0;
              const observer = new MutationObserver(records => { mutations += records.length; });
              observer.observe(root, {
                attributes: true, childList: true, characterData: true, subtree: true
              });
              await new Promise(resolve => setTimeout(resolve, stabilityMs));
              const after = styleSignature();
              mutations += observer.takeRecords().length;
              observer.disconnect();
              const active = root.ownerDocument.getAnimations({subtree: true});
              if (active.length) throw new Error(`active animations before measure: ${active.length}`);
              if (mutations || before !== after) {
                throw new Error(`visual DOM/style changed before measure: ${mutations}`);
              }
            }""",
            160,
        )
        payload = await frame.locator("body").evaluate(
            """(body, config) => {
              const required = config.required;
              const approvedActionKeys = new Set(config.approvedActionKeys);
              const root = document.documentElement;
              const composedParent = node => {
                if (node.parentElement) return node.parentElement;
                const ownerRoot = node.getRootNode?.();
                return ownerRoot instanceof ShadowRoot ? ownerRoot.host : null;
              };
              const imageMapGeometry = area => {
                const map = area.closest('map');
                const mapName = (map?.getAttribute('name') || map?.id || '').trim();
                if (!mapName) return null;
                const image = Array.from(document.querySelectorAll('img[usemap]')).find(candidate =>
                  (candidate.getAttribute('usemap') || '').replace(/^#/, '').toLowerCase()
                    === mapName.toLowerCase()
                );
                if (!image) return null;
                const imageRect = image.getBoundingClientRect();
                const coordinateWidth = Number(image.getAttribute('width'))
                  || image.naturalWidth || imageRect.width;
                const coordinateHeight = Number(image.getAttribute('height'))
                  || image.naturalHeight || imageRect.height;
                if (imageRect.width <= 0 || imageRect.height <= 0
                    || coordinateWidth <= 0 || coordinateHeight <= 0) return null;
                const coordinates = (area.getAttribute('coords') || '')
                  .split(',').map(value => Number(value.trim())).filter(Number.isFinite);
                const shape = (area.getAttribute('shape') || 'rect').toLowerCase();
                let bounds;
                if (shape === 'default') {
                  bounds = [0, 0, coordinateWidth, coordinateHeight];
                } else if (shape === 'circle' && coordinates.length >= 3) {
                  const [x, y, radius] = coordinates;
                  bounds = [x - radius, y - radius, x + radius, y + radius];
                } else if (shape === 'poly' && coordinates.length >= 6) {
                  const xs = coordinates.filter((_value, index) => index % 2 === 0);
                  const ys = coordinates.filter((_value, index) => index % 2 === 1);
                  bounds = [Math.min(...xs), Math.min(...ys), Math.max(...xs), Math.max(...ys)];
                } else if (coordinates.length >= 4) {
                  bounds = [coordinates[0], coordinates[1], coordinates[2], coordinates[3]];
                } else {
                  return null;
                }
                const scaleX = imageRect.width / coordinateWidth;
                const scaleY = imageRect.height / coordinateHeight;
                const x1 = Math.max(0, Math.min(coordinateWidth, Math.min(bounds[0], bounds[2])));
                const y1 = Math.max(0, Math.min(coordinateHeight, Math.min(bounds[1], bounds[3])));
                const x2 = Math.max(0, Math.min(coordinateWidth, Math.max(bounds[0], bounds[2])));
                const y2 = Math.max(0, Math.min(coordinateHeight, Math.max(bounds[1], bounds[3])));
                const left = imageRect.left + x1 * scaleX;
                const top = imageRect.top + y1 * scaleY;
                const right = imageRect.left + x2 * scaleX;
                const bottom = imageRect.top + y2 * scaleY;
                return {
                  clipNode: image,
                  rect: {x:left, y:top, left, top, right, bottom,
                    width:Math.max(0, right-left), height:Math.max(0, bottom-top)}
                };
              };
              const sourceGeometry = node => {
                if (node instanceof HTMLAreaElement) {
                  return imageMapGeometry(node) || {
                    clipNode: node,
                    rect: node.getBoundingClientRect()
                  };
                }
                return {clipNode: node, rect: node.getBoundingClientRect()};
              };
              const effectiveRect = (node, requireHitTest = false) => {
                const geometry = sourceGeometry(node);
                const original = geometry.rect;
                let left = Math.max(0, original.left);
                let top = Math.max(0, original.top);
                let right = Math.min(innerWidth, original.right);
                let bottom = Math.min(innerHeight, original.bottom);
                let clipPathApplied = false;
                let interactionBlocked = Boolean(
                  node.matches?.(':disabled') || node.getAttribute?.('aria-disabled') === 'true'
                );
                for (let current = geometry.clipNode; current; current = composedParent(current)) {
                  const style = getComputedStyle(current);
                  clipPathApplied = clipPathApplied
                    || (style.clipPath && style.clipPath !== 'none')
                    || (style.webkitClipPath && style.webkitClipPath !== 'none');
                  interactionBlocked = interactionBlocked || style.pointerEvents === 'none';
                }
                for (let ancestor = composedParent(geometry.clipNode); ancestor; ancestor = composedParent(ancestor)) {
                  const style = getComputedStyle(ancestor);
                  const ancestorRect = ancestor.getBoundingClientRect();
                  const clipLeft = ancestorRect.left + ancestor.clientLeft;
                  const clipTop = ancestorRect.top + ancestor.clientTop;
                  const clipRight = clipLeft + ancestor.clientWidth;
                  const clipBottom = clipTop + ancestor.clientHeight;
                  if (['hidden', 'clip', 'auto', 'scroll'].includes(style.overflowX)) {
                    left = Math.max(left, clipLeft);
                    right = Math.min(right, clipRight);
                  }
                  if (['hidden', 'clip', 'auto', 'scroll'].includes(style.overflowY)) {
                    top = Math.max(top, clipTop);
                    bottom = Math.min(bottom, clipBottom);
                  }
                }
                if ((clipPathApplied || requireHitTest) && right > left && bottom > top) {
                  const columns = clipPathApplied ? 25 : 3;
                  const rows = clipPathApplied ? 25 : 3;
                  const cellWidth = (right - left) / columns;
                  const cellHeight = (bottom - top) / rows;
                  const hitOwner = geometry.clipNode;
                  const hits = [];
                  for (let row = 0; row < rows; row += 1) {
                    for (let column = 0; column < columns; column += 1) {
                      const x = left + (column + 0.5) * cellWidth;
                      const y = top + (row + 0.5) * cellHeight;
                      const target = document.elementFromPoint(x, y);
                      if (target && (target === hitOwner || hitOwner.contains(target))) {
                        hits.push({x, y});
                      }
                    }
                  }
                  if (!hits.length) {
                    interactionBlocked = true;
                  } else {
                    left = Math.max(left, Math.min(...hits.map(point => point.x)) - cellWidth / 2);
                    right = Math.min(right, Math.max(...hits.map(point => point.x)) + cellWidth / 2);
                    top = Math.max(top, Math.min(...hits.map(point => point.y)) - cellHeight / 2);
                    bottom = Math.min(bottom, Math.max(...hits.map(point => point.y)) + cellHeight / 2);
                  }
                }
                return {
                  x: left,
                  y: top,
                  width: Math.max(0, right - left),
                  height: Math.max(0, bottom - top),
                  clipped: interactionBlocked || Math.abs(left - original.left) > 1 || Math.abs(top - original.top) > 1 ||
                    Math.abs(right - original.right) > 1 || Math.abs(bottom - original.bottom) > 1
                };
              };
              const visible = node => {
                const geometry = sourceGeometry(node);
                const rect = effectiveRect(node);
                if (node.hidden) return false;
                for (let current = geometry.clipNode; current; current = composedParent(current)) {
                  const style = getComputedStyle(current);
                  if (style.display === 'none' || style.visibility === 'hidden'
                      || Number(style.opacity) === 0) return false;
                }
                return rect.width > 0 && rect.height > 0;
              };
              const record = (name, node, requireHitTest = false) => {
                if (!node) return null;
                const rect = effectiveRect(node, requireHitTest);
                return {region:name,x:rect.x,y:rect.y,width:rect.width,height:rect.height,client_width:node.clientWidth,scroll_width:node.scrollWidth,client_height:node.clientHeight,scroll_height:node.scrollHeight,visible:visible(node),clipped:rect.clipped,overlaps:[]};
              };
              const regions = required.map(name => record(name, document.querySelector(`[data-region="${name}"]`)));
              const namedActions = [
                ['action.launcher', document.querySelector('[data-region="launcher"]')],
                ['action.close', document.querySelector('[data-action="close"], [aria-label*="Закрыть" i]')],
                ['action.send', document.querySelector('[data-action="send"], [data-region="composer"] button')],
                ['action.retry', document.querySelector('[data-kaigo-runtime-retry="true"]')]
              ];
              namedActions.forEach(([name, node]) => { if (node) regions.push(record(name, node)); });
              Array.from(document.querySelectorAll('[data-suggestion], [data-region="suggestions"] button')).forEach((node, index) => regions.push(record(`action.suggestion.${index}`, node)));
              const ariaActionSelector = [
                '[role="button"]', '[role="link"]', '[role="checkbox"]',
                '[role="menuitem"]', '[role="menuitemcheckbox"]', '[role="menuitemradio"]',
                '[role="option"]', '[role="radio"]', '[role="switch"]', '[role="tab"]',
                '[role="treeitem"]', '[role="slider"]', '[role="spinbutton"]'
              ].join(',');
              const interactiveSelector = [
                'button', 'a[href]', 'area[href]', 'label[for]', 'input', 'textarea', 'select', 'summary',
                'audio[controls]', 'video[controls]', 'iframe', 'object', 'embed',
                '[contenteditable]:not([contenteditable="false" i])',
                ariaActionSelector,
                '[tabindex]:not([tabindex="-1"])'
              ].join(',');
              const nativeActivatableSelector = [
                'button', 'a[href]', 'area[href]', 'label[for]', 'summary', 'audio[controls]',
                'video[controls]', 'iframe', 'object', 'embed',
                'input[type="button"]', 'input[type="submit"]', 'input[type="reset"]',
                'input[type="image"]', 'input[type="checkbox"]', 'input[type="radio"]',
                'input[type="file"]', 'input[type="range"]', 'input[type="color"]',
                'input[type="number"]',
                'input[type="date"]', 'input[type="datetime-local"]', 'input[type="month"]',
                'input[type="week"]', 'input[type="time"]', 'select'
              ].join(',');
              const implicitLabelNodes = Array.from(
                document.querySelectorAll('label:not([for])')
              ).filter(node => node.querySelector(
                'button,input:not([type="hidden" i]),meter,output,progress,select,textarea'
              ));
              const customClickNodes = Array.from(document.querySelectorAll('*')).filter(node =>
                node.hasAttribute('onclick')
                  || typeof node.onclick === 'function'
                  || globalThis.__kaigoAuditHasClickListener?.(node)
              );
              const knownActionSelector = [
                '[data-region="launcher"]', '[data-action="close"]', '[data-action="send"]',
                '[data-kaigo-runtime-retry="true"]', '[data-suggestion]',
                '[data-region="suggestions"] button'
              ].join(',');
              const behaviorApproved = node => approvedActionKeys.has(
                globalThis.__kaigoAuditActionKey(node)
              );
              const ariaActionNodes = Array.from(document.querySelectorAll(ariaActionSelector));
              const invalidAriaNodes = ariaActionNodes.filter(node => {
                if (node.matches(nativeActivatableSelector)) return false;
                const focusable = node.tabIndex >= 0 && !node.matches(':disabled')
                  && node.getAttribute('aria-disabled') !== 'true';
                return !(focusable && behaviorApproved(node));
              });
              const invalidNativeNodes = Array.from(
                document.querySelectorAll('button,input[type="button" i]')
              ).filter(node => {
                if (node.matches(knownActionSelector) || behaviorApproved(node)) return false;
                if (node instanceof HTMLButtonElement) {
                  const type = (node.getAttribute('type') || 'submit').toLowerCase();
                  const formBehavior = Boolean(node.form) && ['submit', 'reset'].includes(type);
                  const targetBehavior = node.hasAttribute('popovertarget')
                    || node.hasAttribute('commandfor');
                  return !(formBehavior || targetBehavior);
                }
                return true;
              });
              const invalidCustomNodes = customClickNodes.filter(node =>
                !node.matches(nativeActivatableSelector)
                  && !node.matches(ariaActionSelector)
                  && !node.querySelector(nativeActivatableSelector)
                  && !node.querySelector(ariaActionSelector)
                  && !behaviorApproved(node)
              );
              const interactiveNodes = Array.from(new Set([
                ...document.querySelectorAll(interactiveSelector),
                ...implicitLabelNodes,
                ...customClickNodes
              ])).filter(node => !(
                node instanceof HTMLLabelElement
                  && node.control
                  && visible(node.control)
              ));
              const targetDescriptor = node => String(
                node.getAttribute('data-action')
                  || node.getAttribute('data-region')
                  || node.getAttribute('role')
                  || node.tagName.toLowerCase()
              ).replace(/[^a-z0-9_-]+/gi, '_').slice(0, 40);
              interactiveNodes
                .forEach((node, index) => regions.push(record(
                  `target.interactive.${index}.${targetDescriptor(node)}`, node, true
                )));
              invalidAriaNodes
                .forEach((node, index) => regions.push(record(`invalid.aria-action.${index}`, node, true)));
              invalidNativeNodes
                .forEach((node, index) => regions.push(record(`invalid.native-action.${index}`, node, true)));
              invalidCustomNodes
                .forEach((node, index) => regions.push(record(`invalid.custom-action.${index}`, node, true)));
              const visibleActionNodes = interactiveNodes
                .filter(node => {
                  const customAction = customClickNodes.includes(node)
                    && !node.matches(nativeActivatableSelector)
                    && !node.matches(ariaActionSelector)
                    && !node.querySelector(nativeActivatableSelector)
                    && !node.querySelector(ariaActionSelector)
                    && !invalidCustomNodes.includes(node);
                  const validAriaAction = node.matches(ariaActionSelector)
                    && !invalidAriaNodes.includes(node);
                  return (node.matches(nativeActivatableSelector)
                    || implicitLabelNodes.includes(node)
                    || validAriaAction
                    || customAction) && visible(node);
                });
              const visibleActionCount = new Set(visibleActionNodes.map(node =>
                node instanceof HTMLLabelElement && node.control ? node.control : node
              )).size;
              const messages = document.querySelector('[data-region="messages"]');
              const composer = document.querySelector('[data-region="composer"]');
              const panel = document.querySelector('[data-region="panel"]');
              const panelRect = panel && panel.getBoundingClientRect();
              const messageRect = messages && messages.getBoundingClientRect();
              const composerRect = composer && composer.getBoundingClientRect();
              if (messageRect && composerRect && messageRect.bottom > composerRect.top + 1 && messageRect.top < composerRect.bottom - 1) {
                const message = regions.find(item => item && item.region === 'messages');
                const compose = regions.find(item => item && item.region === 'composer');
                if (message) message.overlaps.push('composer');
                if (compose) compose.overlaps.push('messages');
              }
              return {
                regions,
                horizontalOverflow: Math.max(0, root.scrollWidth - root.clientWidth, body.scrollWidth - body.clientWidth),
                panelInside: Boolean(panelRect && panelRect.left >= -1 && panelRect.top >= -1 && panelRect.right <= innerWidth + 1 && panelRect.bottom <= innerHeight + 1),
                transcriptScrollable: Boolean(messages && messages.scrollHeight - messages.clientHeight > 1),
                visibleActionCount,
                activeElement: document.activeElement ? `${document.activeElement.tagName.toLowerCase()}${document.activeElement.getAttribute('data-region') ? '[data-region=' + document.activeElement.getAttribute('data-region') + ']' : ''}` : 'body',
                roles: Array.from(document.querySelectorAll('[data-kaigo-runtime-message]')).map(node => node.dataset.kaigoRuntimeMessage),
                ariaStates: [
                  `launcher.aria-expanded=${document.querySelector('[data-region="launcher"]')?.getAttribute('aria-expanded') ?? 'missing'}`,
                  `panel.aria-hidden=${document.querySelector('[data-region="panel"]')?.getAttribute('aria-hidden') ?? 'missing'}`,
                  `messages.role=${messages?.getAttribute('role') ?? 'missing'}`,
                  `messages.aria-live=${messages?.getAttribute('aria-live') ?? 'missing'}`,
                  `composer.aria-busy=${composer?.getAttribute('aria-busy') ?? 'missing'}`
                ]
              };
            }""",
            {
                "required": list(_REQUIRED_REGIONS),
                "approvedActionKeys": list(approved_action_keys),
            },
        )
        if any(item is None for item in payload["regions"][: len(_REQUIRED_REGIONS)]):
            missing = [
                name
                for name, item in zip(_REQUIRED_REGIONS, payload["regions"])
                if item is None
            ]
            raise ValueError("missing data-region: " + ", ".join(missing))
        return LayoutEvidence(
            evidence_id="layout-" + state.value,
            state=state,
            screenshot_id=screenshot_id,
            viewport_width=width,
            viewport_height=height,
            regions=tuple(RegionEvidence.from_dict(item) for item in payload["regions"]),
            horizontal_overflow_px=payload["horizontalOverflow"],
            panel_inside_viewport=payload["panelInside"],
            first_open_transcript_scrollable=(
                payload["transcriptScrollable"] if state.value.endswith("open_initial") else False
            ),
            visible_action_count=payload["visibleActionCount"],
            active_element=payload["activeElement"],
            transcript_roles=tuple(payload["roles"]),
            aria_states=tuple(payload["ariaStates"]),
            console_errors=tuple(failures.console),
            page_errors=tuple(failures.page),
            request_failures=tuple(failures.requests),
        )

    def _raise_policy_failures(self, failures: _RuntimeFailures) -> None:
        if failures.policy or failures.requests:
            diagnostic = "; ".join((failures.policy + failures.requests)[:10])
            raise BrowserAuditError(
                "unexpected_network",
                "Недоверенный preview попытался использовать сеть или браузерный побочный канал",
                diagnostic=diagnostic,
            )
        if failures.console or failures.page:
            raise BrowserAuditError(
                "browser_runtime_failed",
                "Preview завершился с ошибкой браузерного runtime",
                diagnostic="; ".join((failures.console + failures.page)[:10]),
            )

    def _assert_release_gate(self, report: BrowserAuditReport) -> None:
        failures: list[str] = []
        for layout in report.layouts:
            regions = {region.region: region for region in layout.regions}
            panel = regions.get("panel")
            launcher = regions.get("launcher")
            interactive_targets = [
                item
                for item in layout.regions
                if item.region.startswith("target.interactive.") and item.visible
            ]
            invalid_aria_actions = [
                item
                for item in layout.regions
                if item.region.startswith("invalid.aria-action.")
                and item.visible
                and not item.clipped
            ]
            invalid_native_actions = [
                item
                for item in layout.regions
                if item.region.startswith("invalid.native-action.")
                and item.visible
                and not item.clipped
            ]
            invalid_custom_actions = [
                item
                for item in layout.regions
                if item.region.startswith("invalid.custom-action.")
                and item.visible
                and not item.clipped
            ]
            if layout.horizontal_overflow_px > 1:
                failures.append(f"{layout.state.value}: horizontal overflow > 1px")
            if layout.console_errors or layout.page_errors or layout.request_failures:
                failures.append(f"{layout.state.value}: browser errors are present")
            undersized_targets = [
                item
                for item in interactive_targets
                if item.width < _MIN_INTERACTIVE_TARGET_PX
                or item.height < _MIN_INTERACTIVE_TARGET_PX
            ]
            if undersized_targets:
                details = ", ".join(
                    f"{item.region}={item.width:.2f}×{item.height:.2f}px"
                    for item in undersized_targets[:4]
                )
                failures.append(
                    f"{layout.state.value}: every interactive target must be at least "
                    f"44×44px; undersized: {details}"
                )
            if invalid_aria_actions:
                failures.append(
                    f"{layout.state.value}: every ARIA action must be focusable and support pointer and keyboard activation"
                )
            if invalid_native_actions:
                failures.append(
                    f"{layout.state.value}: every generic native button must expose activation behavior"
                )
            if invalid_custom_actions:
                failures.append(
                    f"{layout.state.value}: every generic custom action must expose activation behavior"
                )
            if layout.state.value.endswith("closed"):
                if panel and panel.visible:
                    failures.append(f"{layout.state.value}: panel must be hidden")
                if not launcher or not launcher.visible:
                    failures.append(f"{layout.state.value}: launcher must be visible")
                elif (
                    abs(launcher.width - 216) > 1
                    or abs(launcher.height - 46) > 1
                    or abs(layout.viewport_width - (launcher.x + launcher.width) - (20 if layout.state.value.startswith('desktop') else 12)) > 1
                    or abs(layout.viewport_height - (launcher.y + launcher.height) - (20 if layout.state.value.startswith('desktop') else 12)) > 1
                ):
                    expected_margin = 20 if layout.state.value.startswith("desktop") else 12
                    actual_right = layout.viewport_width - (launcher.x + launcher.width)
                    actual_bottom = layout.viewport_height - (launcher.y + launcher.height)
                    failures.append(
                        f"{layout.state.value}: launcher must be 216×46px with "
                        f"{expected_margin}px right/bottom margins; actual "
                        f"{launcher.width:.1f}×{launcher.height:.1f}px, right "
                        f"{actual_right:.1f}px, bottom {actual_bottom:.1f}px"
                    )
                launcher_action = regions.get("action.launcher")
                if (
                    not launcher_action
                    or launcher_action.width < _MIN_INTERACTIVE_TARGET_PX
                    or launcher_action.height < _MIN_INTERACTIVE_TARGET_PX
                ):
                    failures.append(f"{layout.state.value}: launcher action must be at least 44×44px")
                if layout.visible_action_count != 1:
                    failures.append(f"{layout.state.value}: launcher must be the only visible action")
                if len(interactive_targets) != 1:
                    failures.append(f"{layout.state.value}: launcher must be the only visible interactive target")
                if "launcher.aria-expanded=false" not in layout.aria_states or "panel.aria-hidden=true" not in layout.aria_states:
                    failures.append(f"{layout.state.value}: closed ARIA state is invalid")
                continue
            if panel is None:
                failures.append(f"{layout.state.value}: panel region is missing")
                continue
            if not panel.visible:
                failures.append(f"{layout.state.value}: open panel must be visible")
                continue
            if not layout.panel_inside_viewport:
                right = layout.viewport_width - (panel.x + panel.width)
                bottom = layout.viewport_height - (panel.y + panel.height)
                failures.append(
                    f"{layout.state.value}: panel must fit inside viewport; panel rect="
                    f"(x={panel.x:.1f}, y={panel.y:.1f}, width={panel.width:.1f}, "
                    f"height={panel.height:.1f}), viewport={layout.viewport_width}x"
                    f"{layout.viewport_height}, right={right:.1f}, bottom={bottom:.1f}; "
                    "put position: fixed on the panel itself, remove transforms and "
                    "viewport offsets from the root, and set explicit right/bottom offsets"
                )
                continue
            if panel.clipped:
                failures.append(f"{layout.state.value}: panel is clipped")
            if any(region.overlaps for region in layout.regions if region.region in {"messages", "composer"}):
                failures.append(f"{layout.state.value}: composer overlaps messages")
            actions = [item for item in layout.regions if item.region.startswith("action.") and item.visible]
            suggestions = [
                item
                for item in layout.regions
                if item.region.startswith("action.suggestion.") and item.visible
            ]
            close_action = regions.get("action.close")
            send_action = regions.get("action.send")
            retry_action = regions.get("action.retry")
            if (
                close_action is None
                or send_action is None
                or not close_action.visible
                or not send_action.visible
            ):
                failures.append(f"{layout.state.value}: close and send actions are required")
            undersized_actions = [
                item
                for item in actions
                if item.width < _MIN_INTERACTIVE_TARGET_PX
                or item.height < _MIN_INTERACTIVE_TARGET_PX
            ]
            if not actions or undersized_actions:
                details = ", ".join(
                    f"{item.region}={item.width:.2f}×{item.height:.2f}px"
                    for item in undersized_actions[:4]
                )
                suffix = f"; undersized: {details}" if details else ""
                failures.append(
                    f"{layout.state.value}: every action must be at least 44×44px{suffix}"
                )
            if layout.visible_action_count > 3:
                failures.append(
                    f"{layout.state.value}: no more than three actions total may be "
                    "visible, including close/send/retry/suggestions"
                )
            if len(suggestions) > 2:
                failures.append(
                    f"{layout.state.value}: no more than two suggestions are allowed"
                )
            clipped_actions = [
                item
                for item in actions
                if (
                    item.clipped
                    or item.x < panel.x - 1
                    or item.y < panel.y - 1
                    or item.x + item.width > panel.x + panel.width + 1
                    or item.y + item.height > panel.y + panel.height + 1
                )
            ]
            if clipped_actions:
                details = ", ".join(item.region for item in clipped_actions[:4])
                failures.append(
                    f"{layout.state.value}: every open action must remain unclipped "
                    f"and inside the panel; offending: {details}"
                )
            clipped_targets = [
                item
                for item in interactive_targets
                if (
                item.clipped
                or item.x < panel.x - 1
                or item.y < panel.y - 1
                or item.x + item.width > panel.x + panel.width + 1
                or item.y + item.height > panel.y + panel.height + 1
                )
            ]
            if clipped_targets:
                details = ", ".join(item.region for item in clipped_targets[:4])
                failures.append(
                    f"{layout.state.value}: every interactive target must remain "
                    f"unclipped and inside the panel; offending: {details}"
                )
            if layout.state.value.endswith("open_initial") and layout.first_open_transcript_scrollable:
                messages_region = regions.get("messages")
                metrics = (
                    f"; messages scrollHeight {messages_region.scroll_height}px exceeds "
                    f"clientHeight {messages_region.client_height}px"
                    if messages_region is not None
                    else ""
                )
                failures.append(
                    f"{layout.state.value}: first-open transcript must not scroll{metrics}; "
                    "keep the exact first-open panel geometry and shorten the welcome copy, "
                    "show at most one short suggestion, and reduce nonessential gaps/padding "
                    "without shrinking any action below 44x44px; reset browser-default p and "
                    "heading margins to 0"
                )
            is_mobile_initial = layout.state is LayoutState.MOBILE_OPEN_INITIAL
            if is_mobile_initial and "textarea" in layout.active_element:
                failures.append(f"{layout.state.value}: composer must not auto-focus on mobile first open")
            elif not is_mobile_initial and "textarea" not in layout.active_element:
                failures.append(f"{layout.state.value}: composer textarea must retain focus")
            if "launcher.aria-expanded=true" not in layout.aria_states or "panel.aria-hidden=false" not in layout.aria_states:
                failures.append(f"{layout.state.value}: open ARIA state is invalid")
            aria_states = set(layout.aria_states)
            busy_state_valid = (
                "composer.aria-busy=false" in aria_states
                if "after_turn" in layout.state.value
                else bool(
                    {"composer.aria-busy=false", "composer.aria-busy=missing"}
                    & aria_states
                )
            )
            if not {
                "messages.role=log",
                "messages.aria-live=polite",
            }.issubset(aria_states) or not busy_state_valid:
                failures.append(f"{layout.state.value}: live transcript ARIA state is invalid")
            for required in _REQUIRED_REGIONS:
                region = regions.get(required)
                if required == "root":
                    continue
                if region is None or not region.visible:
                    if required != "launcher":
                        failures.append(f"{layout.state.value}: required region {required} is not visible")
                    continue
                if region.clipped or region.scroll_width - region.client_width > 1:
                    failures.append(f"{layout.state.value}: required region {required} is clipped or horizontally overflowing")
            if layout.state.value.endswith("after_turn_1") and layout.transcript_roles != (
                "user",
                "assistant",
            ):
                failures.append(f"{layout.state.value}: first turn order is invalid")
            if layout.state.value.endswith("after_turn_2") and layout.transcript_roles != (
                "user",
                "assistant",
                "user",
                "assistant",
            ):
                failures.append(f"{layout.state.value}: second turn order is invalid")
            if layout.state.value.startswith("desktop"):
                if abs(panel.width - 372) > 1:
                    failures.append(
                        f"{layout.state.value}: desktop panel width must be 372px; "
                        f"actual {panel.width:.1f}px; set box-sizing:border-box on the "
                        "panel selector itself (or a matching descendant rule), because "
                        "box-sizing on the root does not inherit"
                    )
                if panel.height > min(536, layout.viewport_height * 0.68) + 1:
                    failures.append(
                        f"{layout.state.value}: desktop panel exceeds height cap; "
                        f"actual {panel.height:.1f}px; max-height must include borders via "
                        "box-sizing:border-box"
                    )
                if abs(layout.viewport_width - (panel.x + panel.width) - 20) > 1 or abs(layout.viewport_height - (panel.y + panel.height) - 20) > 1:
                    actual_right = layout.viewport_width - (panel.x + panel.width)
                    actual_bottom = layout.viewport_height - (panel.y + panel.height)
                    failures.append(
                        f"{layout.state.value}: desktop panel must keep 20px right/bottom "
                        f"margins; actual right {actual_right:.1f}px, bottom {actual_bottom:.1f}px; "
                        "make the panel itself position:fixed and do not compound offsets on the root"
                    )
            else:
                expected_mobile_width = layout.viewport_width - 24
                if abs(panel.width - expected_mobile_width) > 1 or panel.x < 11 or panel.x + panel.width > layout.viewport_width - 11:
                    failures.append(
                        f"{layout.state.value}: mobile panel must be calc(100vw - 24px) "
                        f"({expected_mobile_width}px here) and keep 12px margins; actual "
                        f"width {panel.width:.1f}px, x {panel.x:.1f}px"
                    )
                if panel.height > layout.viewport_height * 0.70 + 1 or panel.height >= layout.viewport_height - 1:
                    failures.append(f"{layout.state.value}: mobile panel exceeds 70dvh/non-fullscreen cap")
                if abs(layout.viewport_height - (panel.y + panel.height) - 12) > 1:
                    actual_bottom = layout.viewport_height - (panel.y + panel.height)
                    failures.append(
                        f"{layout.state.value}: mobile panel must keep 12px bottom margin; "
                        f"actual {actual_bottom:.1f}px; make the panel itself position:fixed "
                        "and do not compound offsets on the root"
                    )
        if failures:
            raise BrowserAuditError(
                "browser_gate_failed",
                "Виджет не прошёл детерминированную браузерную проверку: " + "; ".join(failures[:8]),
                diagnostic="\n".join(failures),
                failures=tuple(failures),
            )


__all__ = [
    "BrowserAudit",
    "BrowserAuditError",
    "BrowserAuditReport",
    "CapturedScreenshot",
    "MAX_INLINE_BYTES",
    "MAX_SCREENSHOT_BYTES",
]
