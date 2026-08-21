from __future__ import annotations

import asyncio
import json
import math
from dataclasses import dataclass
from functools import partial
from typing import Any

from playwright.async_api import Browser, async_playwright

from .browser_audit import BrowserAudit
from .models import WidgetArtifact
from .preview import build_trusted_runtime_document


_VIEWPORTS = (("desktop", 1920, 1080, True), ("mobile", 390, 844, False))
_SAMPLE_FRACTIONS = (0.05, 0.5, 0.95)
_HOVER_DURATION = (80.0, 600.0)
_CLOSE_DURATION = (120.0, 900.0)
_DRAFT = "Черновик для проверки закрытия"


class RefinementMotionAuditError(RuntimeError):
    """A deterministic refinement-motion requirement was not met."""


@dataclass(frozen=True)
class MotionFrameEvidence:
    fraction: float
    x: float
    y: float
    width: float
    height: float
    transform: str
    opacity: str
    filter: str
    box_shadow: str
    clip_path: str
    visibility: str
    display: str

    @classmethod
    def from_payload(cls, fraction: float, payload: dict[str, Any]) -> MotionFrameEvidence:
        rect = payload["rect"]
        return cls(
            fraction=fraction,
            x=float(rect["x"]),
            y=float(rect["y"]),
            width=float(rect["width"]),
            height=float(rect["height"]),
            transform=str(payload["transform"]),
            opacity=str(payload["opacity"]),
            filter=str(payload["filter"]),
            box_shadow=str(payload["boxShadow"]),
            clip_path=str(payload["clipPath"]),
            visibility=str(payload["visibility"]),
            display=str(payload["display"]),
        )

    @property
    def signature(self) -> str:
        return json.dumps(
            {
                "rect": [
                    round(self.x, 3),
                    round(self.y, 3),
                    round(self.width, 3),
                    round(self.height, 3),
                ],
                "transform": self.transform,
                "opacity": self.opacity,
                "filter": self.filter,
                "box_shadow": self.box_shadow,
                "clip_path": self.clip_path,
                "visibility": self.visibility,
                "display": self.display,
            },
            ensure_ascii=False,
            sort_keys=True,
        )


@dataclass(frozen=True)
class ActionMotionEvidence:
    action: str
    duration_ms: float
    distinct_frame_count: int
    frames: tuple[MotionFrameEvidence, ...]
    restored_baseline: bool = False
    terminal_closed: bool = False
    reopens_with_transcript: bool = False
    reopens_with_draft: bool = False


@dataclass(frozen=True)
class ReducedMotionEvidence:
    continuing_animation_count: int
    checked_actions: tuple[str, ...]


@dataclass(frozen=True)
class ViewportMotionEvidence:
    name: str
    width: int
    height: int
    hover: ActionMotionEvidence | None
    close: ActionMotionEvidence
    reduced_motion: ReducedMotionEvidence


@dataclass(frozen=True)
class RefinementMotionReport:
    assistant_label: str
    viewports: tuple[ViewportMotionEvidence, ...]

    def __post_init__(self) -> None:
        if tuple(item.name for item in self.viewports) != ("desktop", "mobile"):
            raise ValueError("motion report requires ordered desktop and mobile evidence")

    @property
    def desktop(self) -> ViewportMotionEvidence:
        return self.viewports[0]

    @property
    def mobile(self) -> ViewportMotionEvidence:
        return self.viewports[1]

    @property
    def hover(self) -> ActionMotionEvidence:
        hover = self.desktop.hover
        if hover is None:  # pragma: no cover - guarded by the report contract
            raise RuntimeError("desktop hover evidence is missing")
        return hover

    @property
    def close(self) -> ActionMotionEvidence:
        return self.desktop.close

    @property
    def reduced_motion(self) -> ReducedMotionEvidence:
        return ReducedMotionEvidence(
            continuing_animation_count=sum(
                item.reduced_motion.continuing_animation_count
                for item in self.viewports
            ),
            checked_actions=tuple(
                action
                for item in self.viewports
                for action in item.reduced_motion.checked_actions
            ),
        )


class RefinementMotionAudit:
    """Audit refinement motion in the same trusted runtime used for publication."""

    def __init__(
        self,
        *,
        assistant_label: str,
        browser: Browser | None = None,
        timeout_ms: int = 10_000,
        total_timeout_seconds: float = 90,
    ) -> None:
        if total_timeout_seconds <= 0 or total_timeout_seconds > 180:
            raise ValueError("total_timeout_seconds is invalid")
        if not isinstance(assistant_label, str):
            raise TypeError("assistant_label must be a string")
        assistant_label = assistant_label.strip()
        if not assistant_label or len(assistant_label) > 80 or "\x00" in assistant_label:
            raise ValueError("assistant_label is invalid")
        self._assistant_label = assistant_label
        self._browser = browser
        self._total_timeout_seconds = total_timeout_seconds
        self._browser_audit = BrowserAudit(
            browser=browser,
            document_builder=partial(
                build_trusted_runtime_document,
                assistant_label=assistant_label,
            ),
            timeout_ms=timeout_ms,
            total_timeout_seconds=total_timeout_seconds,
        )

    async def audit(self, artifact: WidgetArtifact) -> RefinementMotionReport:
        if not isinstance(artifact, WidgetArtifact):
            raise TypeError("artifact must be WidgetArtifact")
        playwright = None
        browser = self._browser
        deadline = asyncio.get_running_loop().time() + self._total_timeout_seconds
        try:
            async with asyncio.timeout(self._total_timeout_seconds):
                if browser is None:
                    playwright = await async_playwright().start()
                    browser = await playwright.chromium.launch(
                        headless=True,
                        args=["--force-webrtc-ip-handling-policy=disable_non_proxied_udp"],
                    )
                viewports: list[ViewportMotionEvidence] = []
                for name, width, height, hover_capable in _VIEWPORTS:
                    hover, close = await self._audit_motion_viewport(
                        browser=browser,
                        artifact=artifact,
                        name=name,
                        width=width,
                        height=height,
                        hover_capable=hover_capable,
                        deadline=deadline,
                    )
                    reduced = await self._audit_reduced_motion(
                        browser=browser,
                        artifact=artifact,
                        name=name,
                        width=width,
                        height=height,
                        hover_capable=hover_capable,
                        deadline=deadline,
                    )
                    viewports.append(
                        ViewportMotionEvidence(
                            name=name,
                            width=width,
                            height=height,
                            hover=hover,
                            close=close,
                            reduced_motion=reduced,
                        )
                    )
                return RefinementMotionReport(
                    assistant_label=self._assistant_label,
                    viewports=tuple(viewports),
                )
        except RefinementMotionAuditError:
            raise
        except TimeoutError as exc:
            raise RefinementMotionAuditError("motion audit exceeded its absolute timeout") from exc
        except Exception as exc:
            raise RefinementMotionAuditError(
                f"motion audit failed closed: {type(exc).__name__}: {exc}"
            ) from exc
        finally:
            if self._browser is None and browser is not None:
                await self._browser_audit._bounded_cleanup(
                    browser.close(), self._browser_audit._cleanup_timeout(deadline)
                )
            if playwright is not None:
                await self._browser_audit._bounded_cleanup(
                    playwright.stop(), self._browser_audit._cleanup_timeout(deadline)
                )

    async def _audit_motion_viewport(
        self,
        *,
        browser: Browser,
        artifact: WidgetArtifact,
        name: str,
        width: int,
        height: int,
        hover_capable: bool,
        deadline: float,
    ) -> tuple[ActionMotionEvidence | None, ActionMotionEvidence]:
        context, page, failures = await self._browser_audit._new_context(
            browser,
            width,
            height,
            reduced_motion="no-preference",
            deadline=deadline,
        )
        try:
            frame = await self._browser_audit._mount(
                page,
                artifact,
                freeze_motion=False,
            )
            root = frame.locator('[data-region="root"]')
            launcher = frame.locator('[data-region="launcher"]')
            await root.wait_for(state="attached")
            if await root.get_attribute("data-state") != "closed":
                raise RefinementMotionAuditError(f"{name} widget did not start closed")
            if await root.get_attribute("data-assistant-label") != self._assistant_label:
                raise RefinementMotionAuditError(
                    f"{name} trusted runtime assistant label does not match persona"
                )

            hover = None
            if hover_capable:
                hover = await self._audit_hover(
                    page=page,
                    frame=frame,
                    launcher=launcher,
                    name=name,
                    width=width,
                    height=height,
                )

            await launcher.click()
            if await root.get_attribute("data-state") != "open":
                raise RefinementMotionAuditError(f"{name} launcher did not open widget")
            await self._finish_current_animations(frame, '[data-region="panel"]')

            input_box = frame.locator('[data-kaigo-runtime-input="true"]')
            await input_box.fill("Проверка истории")
            await input_box.press("Enter")
            await self._browser_audit._assert_pending_turn(frame, ("user",))
            await self._browser_audit._release_audit_response(page)
            await frame.locator('[data-kaigo-runtime-message="assistant"]').wait_for()
            await self._browser_audit._assert_completed_turn(frame, ("user", "assistant"))
            expected_transcript = await self._browser_audit._transcript(frame)
            await input_box.fill(_DRAFT)

            await self._mark_action_boundary(frame, '[data-region="panel"]')
            close_control = frame.locator(
                '[data-action="close"], [aria-label*="Закрыть" i]'
            ).first
            await close_control.click()
            close = await self._sample_action(
                frame=frame,
                selector='[data-region="panel"]',
                action="close",
                name=name,
                width=width,
                height=height,
                duration_bounds=_CLOSE_DURATION,
            )
            await self._finish_linked_animations(frame)
            terminal = await frame.locator("body").evaluate(
                """() => {
                  const root = document.querySelector('[data-region="root"]');
                  const panel = document.querySelector('[data-region="panel"]');
                  const launcher = document.querySelector('[data-region="launcher"]');
                  const style = panel ? getComputedStyle(panel) : null;
                  return {
                    state: root?.dataset.state || 'missing',
                    panelAriaHidden: panel?.getAttribute('aria-hidden'),
                    launcherHidden: Boolean(launcher?.hidden),
                    panelTerminalHidden: Boolean(style && (
                      style.display === 'none'
                      || style.visibility === 'hidden'
                      || Number.parseFloat(style.opacity) <= .01
                    ))
                  };
                }"""
            )
            terminal_closed = terminal == {
                "state": "closed",
                "panelAriaHidden": "true",
                "launcherHidden": False,
                "panelTerminalHidden": True,
            }
            if not terminal_closed:
                raise RefinementMotionAuditError(
                    f"{name} close never reached terminal closed state: {terminal}"
                )

            await launcher.click()
            await self._finish_current_animations(frame, '[data-region="panel"]')
            transcript_ok = await self._browser_audit._transcript(frame) == expected_transcript
            draft_ok = await input_box.input_value() == _DRAFT
            if not transcript_ok or not draft_ok:
                lost = "transcript" if not transcript_ok else "draft"
                raise RefinementMotionAuditError(
                    f"{name} close reopen lost {lost}"
                )
            close = ActionMotionEvidence(
                action=close.action,
                duration_ms=close.duration_ms,
                distinct_frame_count=close.distinct_frame_count,
                frames=close.frames,
                terminal_closed=True,
                reopens_with_transcript=True,
                reopens_with_draft=True,
            )
            self._browser_audit._raise_policy_failures(failures)
            return hover, close
        finally:
            await self._browser_audit._bounded_cleanup(
                context.close(), self._browser_audit._cleanup_timeout(deadline)
            )

    async def _audit_hover(
        self,
        *,
        page,
        frame,
        launcher,
        name: str,
        width: int,
        height: int,
    ) -> ActionMotionEvidence:
        baseline = await self._snapshot(frame, '[data-region="launcher"]')
        await self._mark_action_boundary(frame, '[data-region="launcher"]')
        await launcher.hover()
        hover = await self._sample_action(
            frame=frame,
            selector='[data-region="launcher"]',
            action="hover",
            name=name,
            width=width,
            height=height,
            duration_bounds=_HOVER_DURATION,
        )
        await self._finish_linked_animations(frame)
        await self._mark_action_boundary(frame, '[data-region="launcher"]')
        await page.mouse.move(1, 1)
        await self._finish_new_animations(frame, '[data-region="launcher"]')
        restored = self._same_snapshot(
            baseline,
            await self._snapshot(frame, '[data-region="launcher"]'),
        )
        if not restored:
            raise RefinementMotionAuditError(
                f"{name} hover did not restore its baseline"
            )
        return ActionMotionEvidence(
            action=hover.action,
            duration_ms=hover.duration_ms,
            distinct_frame_count=hover.distinct_frame_count,
            frames=hover.frames,
            restored_baseline=True,
        )

    async def _audit_reduced_motion(
        self,
        *,
        browser: Browser,
        artifact: WidgetArtifact,
        name: str,
        width: int,
        height: int,
        hover_capable: bool,
        deadline: float,
    ) -> ReducedMotionEvidence:
        context, page, failures = await self._browser_audit._new_context(
            browser,
            width,
            height,
            reduced_motion="reduce",
            deadline=deadline,
        )
        try:
            frame = await self._browser_audit._mount(
                page,
                artifact,
                freeze_motion=False,
            )
            launcher = frame.locator('[data-region="launcher"]')
            checked: list[str] = []
            continuing = await self._count_continuing_visual_animations(
                frame, '[data-region="launcher"]'
            )
            if continuing:
                raise RefinementMotionAuditError(
                    f"{name} reduced-motion has {continuing} continuing visual animation(s)"
                )
            if hover_capable:
                await self._mark_action_boundary(
                    frame, '[data-region="launcher"]'
                )
                await launcher.hover()
                continuing = await self._count_continuing_visual_animations(
                    frame, '[data-region="launcher"]'
                )
                checked.append("hover")
                if continuing:
                    raise RefinementMotionAuditError(
                        f"{name} reduced-motion has {continuing} continuing visual animation(s)"
                    )
                await page.mouse.move(1, 1)
                await self._cancel_current_animations(frame)

            await launcher.click()
            continuing = await self._count_continuing_visual_animations(
                frame, '[data-region="panel"]'
            )
            if continuing:
                raise RefinementMotionAuditError(
                    f"{name} reduced-motion has {continuing} continuing visual animation(s)"
                )
            await self._finish_current_animations(frame, '[data-region="panel"]')
            await self._mark_action_boundary(frame, '[data-region="panel"]')
            await frame.locator('[data-action="close"]').click()
            continuing = await self._count_continuing_visual_animations(
                frame, '[data-region="panel"]'
            )
            checked.append("close")
            if continuing:
                raise RefinementMotionAuditError(
                    f"{name} reduced-motion has {continuing} continuing visual animation(s)"
                )
            self._browser_audit._raise_policy_failures(failures)
            return ReducedMotionEvidence(
                continuing_animation_count=0,
                checked_actions=tuple(checked),
            )
        finally:
            await self._browser_audit._bounded_cleanup(
                context.close(), self._browser_audit._cleanup_timeout(deadline)
            )

    @staticmethod
    async def _mark_action_boundary(frame, selector: str) -> None:
        await frame.locator("body").evaluate(
            """(_body, selector) => {
              if (typeof window.__kaigoMotionCleanup === 'function') {
                window.__kaigoMotionCleanup();
              }
              const target = document.querySelector(selector);
              if (!target) throw new Error(`motion target is missing: ${selector}`);
              const before = new Set(document.getAnimations({subtree: true}));
              const captured = new Map();
              const isRelevant = animation => {
                const node = animation.effect && animation.effect.target;
                return Boolean(node && (
                  node === target || target.contains(node) || node.contains(target)
                ));
              };
              const capture = () => {
                for (const animation of document.getAnimations({subtree: true})) {
                  if (before.has(animation) || !isRelevant(animation)) continue;
                  if (!captured.has(animation)) {
                    const specified = animation.effect?.getTiming?.() || {};
                    captured.set(animation, {
                      animation,
                      activeAtCapture: ['running', 'pending'].includes(animation.playState)
                        || !Number.isFinite(Number(specified.iterations ?? 1))
                    });
                  }
                  try { animation.pause(); } catch (_) {}
                }
              };
              const scheduleCapture = () => queueMicrotask(capture);
              const listeners = [
                ['pointerover', scheduleCapture],
                ['mouseover', scheduleCapture],
                ['click', scheduleCapture],
                ['transitionrun', capture],
                ['animationstart', capture]
              ];
              for (const [type, listener] of listeners) {
                document.addEventListener(type, listener, true);
              }
              window.__kaigoMotionBefore = before;
              window.__kaigoMotionCaptured = captured;
              window.__kaigoMotionLinked = [];
              window.__kaigoMotionCleanup = () => {
                for (const [type, listener] of listeners) {
                  document.removeEventListener(type, listener, true);
                }
                window.__kaigoMotionCleanup = null;
              };
            }""",
            selector,
        )

    async def _sample_action(
        self,
        *,
        frame,
        selector: str,
        action: str,
        name: str,
        width: int,
        height: int,
        duration_bounds: tuple[float, float],
    ) -> ActionMotionEvidence:
        metadata = await frame.locator("body").evaluate(
            """(_body, selector) => {
              const target = document.querySelector(selector);
              if (!target) return {error: 'target is missing'};
              const before = window.__kaigoMotionBefore || new Set();
              if (typeof window.__kaigoMotionCleanup === 'function') {
                window.__kaigoMotionCleanup();
              }
              const relevant = new Set(
                Array.from(window.__kaigoMotionCaptured?.values?.() || []).map(
                  item => item.animation
                )
              );
              for (const animation of document.getAnimations({subtree: true})) {
                if (before.has(animation)) continue;
                const node = animation.effect && animation.effect.target;
                if (node && (
                  node === target || target.contains(node) || node.contains(target)
                )) relevant.add(animation);
              }
              const records = [];
              for (const animation of relevant) {
                const specified = animation.effect?.getTiming?.() || {};
                const computed = animation.effect?.getComputedTiming?.() || {};
                const iterations = Number(specified.iterations ?? 1);
                const endTime = Number(computed.endTime);
                if (!Number.isFinite(iterations) || !Number.isFinite(endTime)) {
                  return {error: 'infinite action-linked animation'};
                }
                if (iterations < 0 || endTime < 0) {
                  return {error: 'invalid action-linked animation timing'};
                }
                records.push({animation, endTime});
                try { animation.pause(); } catch (_) {}
              }
              const relatedNodes = (() => {
                const nodes = new Set([target]);
                for (const item of records) {
                  const node = item.animation.effect && item.animation.effect.target;
                  if (!(node instanceof Element)) continue;
                  if (node === target || node.contains(target)) {
                    for (let current = target; current; current = current.parentElement) {
                      nodes.add(current);
                      if (current === node) break;
                    }
                  } else if (target.contains(node)) {
                    for (let current = node; current; current = current.parentElement) {
                      nodes.add(current);
                      if (current === target) break;
                    }
                  }
                }
                return Array.from(nodes);
              })();
              const snapshot = () => JSON.stringify(relatedNodes.map(node => {
                const rect = node.getBoundingClientRect();
                const style = getComputedStyle(node);
                return {
                  rect: [rect.x, rect.y, rect.width, rect.height],
                  transform: style.transform,
                  opacity: style.opacity,
                  filter: style.filter,
                  boxShadow: style.boxShadow,
                  clipPath: style.clipPath,
                  visibility: style.visibility,
                  display: style.display
                };
              }));
              for (const item of records) item.animation.currentTime = 0;
              const baseline = snapshot();
              const visual = [];
              for (const item of records) {
                let changed = false;
                for (const fraction of [0, .25, .5, .75, 1]) {
                  for (const candidate of records) candidate.animation.currentTime = 0;
                  item.animation.currentTime = item.endTime * fraction;
                  if (snapshot() !== baseline) {
                    changed = true;
                    break;
                  }
                }
                if (changed) visual.push(item);
              }
              for (const item of records) item.animation.currentTime = 0;
              window.__kaigoMotionLinked = records;
              window.__kaigoMotionDuration = visual.reduce(
                (value, item) => Math.max(value, item.endTime),
                0
              );
              return {
                count: visual.length,
                duration: window.__kaigoMotionDuration
              };
            }""",
            selector,
        )
        if metadata.get("error"):
            raise RefinementMotionAuditError(
                f"{name} {action} {metadata['error']}"
            )
        if int(metadata.get("count", 0)) < 1:
            raise RefinementMotionAuditError(
                f"{name} {action} has no action-linked animation"
            )
        duration = float(metadata.get("duration", 0))
        low, high = duration_bounds
        if not math.isfinite(duration) or not low <= duration <= high:
            raise RefinementMotionAuditError(
                f"{name} {action} animation duration {duration:.1f}ms is outside "
                f"{low:.0f}..{high:.0f}ms"
            )

        frames = []
        for fraction in _SAMPLE_FRACTIONS:
            payload = await frame.locator("body").evaluate(
                """(_body, {selector, fraction}) => {
                  const wallTime = Number(window.__kaigoMotionDuration || 0) * fraction;
                  for (const item of window.__kaigoMotionLinked || []) {
                    item.animation.currentTime = Math.min(item.endTime, wallTime);
                  }
                  const target = document.querySelector(selector);
                  const rect = target.getBoundingClientRect();
                  const style = getComputedStyle(target);
                  let clippedByAncestor = false;
                  for (let node = target.parentElement; node; node = node.parentElement) {
                    const nodeStyle = getComputedStyle(node);
                    const clipsX = ['hidden', 'clip', 'auto', 'scroll'].includes(nodeStyle.overflowX);
                    const clipsY = ['hidden', 'clip', 'auto', 'scroll'].includes(nodeStyle.overflowY);
                    if (!clipsX && !clipsY) continue;
                    const ancestor = node.getBoundingClientRect();
                    if (
                      (clipsX && (rect.left < ancestor.left - .5 || rect.right > ancestor.right + .5))
                      || (clipsY && (rect.top < ancestor.top - .5 || rect.bottom > ancestor.bottom + .5))
                    ) clippedByAncestor = true;
                  }
                  return {
                    rect: {x: rect.x, y: rect.y, width: rect.width, height: rect.height},
                    transform: style.transform,
                    opacity: style.opacity,
                    filter: style.filter,
                    boxShadow: style.boxShadow,
                    clipPath: style.clipPath,
                    visibility: style.visibility,
                    display: style.display,
                    clippedByAncestor,
                    documentOverflow: document.documentElement.scrollWidth > innerWidth + 1
                      || document.documentElement.scrollHeight > innerHeight + 1
                  };
                }""",
                {"selector": selector, "fraction": fraction},
            )
            evidence = MotionFrameEvidence.from_payload(fraction, payload)
            if (
                evidence.width <= 0
                or evidence.height <= 0
                or evidence.x < -1
                or evidence.y < -1
                or evidence.x + evidence.width > width + 1
                or evidence.y + evidence.height > height + 1
                or payload["clippedByAncestor"]
                or payload["documentOverflow"]
            ):
                raise RefinementMotionAuditError(
                    f"{name} {action} frame is outside viewport or clipped"
                )
            frames.append(evidence)
        distinct = len({item.signature for item in frames})
        if distinct < 3:
            raise RefinementMotionAuditError(
                f"{name} {action} has only {distinct} distinct frames"
            )
        return ActionMotionEvidence(
            action=action,
            duration_ms=duration,
            distinct_frame_count=distinct,
            frames=tuple(frames),
        )

    @staticmethod
    async def _snapshot(frame, selector: str) -> dict[str, Any]:
        return await frame.locator("body").evaluate(
            """(_body, selector) => {
              const target = document.querySelector(selector);
              const rect = target.getBoundingClientRect();
              const style = getComputedStyle(target);
              return {
                rect: [rect.x, rect.y, rect.width, rect.height],
                transform: style.transform,
                opacity: style.opacity,
                filter: style.filter,
                boxShadow: style.boxShadow,
                clipPath: style.clipPath
              };
            }""",
            selector,
        )

    @staticmethod
    def _same_snapshot(left: dict[str, Any], right: dict[str, Any]) -> bool:
        if any(left[key] != right[key] for key in left if key != "rect"):
            return False
        return all(
            abs(float(first) - float(second)) <= 0.1
            for first, second in zip(left["rect"], right["rect"], strict=True)
        )

    @staticmethod
    async def _finish_linked_animations(frame) -> None:
        await frame.locator("body").evaluate(
            """() => {
              for (const item of window.__kaigoMotionLinked || []) {
                try { item.animation.finish(); } catch (_) { item.animation.cancel(); }
              }
              return new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
            }"""
        )

    @staticmethod
    async def _finish_new_animations(frame, selector: str) -> None:
        await frame.locator("body").evaluate(
            """(_body, selector) => {
              const target = document.querySelector(selector);
              const before = window.__kaigoMotionBefore || new Set();
              const animations = new Set(document.getAnimations({subtree: true}));
              for (const captured of window.__kaigoMotionCaptured?.values?.() || []) {
                animations.add(captured.animation);
              }
              for (const animation of animations) {
                const node = animation.effect && animation.effect.target;
                if (before.has(animation) || !node) continue;
                if (node === target || target.contains(node) || node.contains(target)) {
                  try { animation.finish(); } catch (_) { animation.cancel(); }
                }
              }
              return new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
            }""",
            selector,
        )

    @staticmethod
    async def _finish_current_animations(frame, selector: str) -> None:
        await frame.locator("body").evaluate(
            """(_body, selector) => {
              const target = document.querySelector(selector);
              for (const animation of document.getAnimations({subtree: true})) {
                const node = animation.effect && animation.effect.target;
                if (node && (node === target || target.contains(node) || node.contains(target))) {
                  try { animation.finish(); } catch (_) { animation.cancel(); }
                }
              }
              return new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve)));
            }""",
            selector,
        )

    @staticmethod
    async def _cancel_current_animations(frame) -> None:
        await frame.locator("body").evaluate(
            """() => {
              for (const animation of document.getAnimations({subtree: true})) {
                try { animation.cancel(); } catch (_) {}
              }
            }"""
        )

    @staticmethod
    async def _count_continuing_visual_animations(frame, selector: str) -> int:
        return int(
            await frame.locator("body").evaluate(
                """(_body, selector) => {
                  const target = document.querySelector(selector);
                  if (!target) return 0;
                  if (typeof window.__kaigoMotionCleanup === 'function') {
                    window.__kaigoMotionCleanup();
                  }
                  const entries = new Map();
                  for (const captured of window.__kaigoMotionCaptured?.values?.() || []) {
                    const node = captured.animation.effect && captured.animation.effect.target;
                    if (!node || !(
                      node === target || target.contains(node) || node.contains(target)
                    )) continue;
                    entries.set(captured.animation, {
                      animation: captured.animation,
                      activeAtCapture: Boolean(captured.activeAtCapture)
                    });
                  }
                  for (const animation of document.getAnimations({subtree: true})) {
                    const node = animation.effect && animation.effect.target;
                    if (!node || !(
                      node === target || target.contains(node) || node.contains(target)
                    )) continue;
                    if (!entries.has(animation)) {
                      entries.set(animation, {animation, activeAtCapture: false});
                    }
                  }
                  const continuing = [];
                  for (const entry of entries.values()) {
                    const animation = entry.animation;
                    const specified = animation.effect?.getTiming?.() || {};
                    const computed = animation.effect?.getComputedTiming?.() || {};
                    const iterations = Number(specified.iterations ?? 1);
                    const endTime = Number(computed.endTime);
                    const active = entry.activeAtCapture
                      || ['running', 'pending'].includes(animation.playState)
                      || !Number.isFinite(iterations)
                      || !Number.isFinite(endTime);
                    if (!active) continue;
                    const duration = Number(specified.duration);
                    const probeTime = Number.isFinite(endTime)
                      ? Math.max(0, endTime)
                      : (Number.isFinite(duration) && duration > 0 ? duration : 1);
                    continuing.push({
                      animation,
                      probeTime,
                      originalTime: animation.currentTime,
                      originalPlayState: animation.playState
                    });
                    try { animation.pause(); } catch (_) {}
                  }
                  if (!continuing.length) return 0;
                  const relatedNodes = (() => {
                    const nodes = new Set([target]);
                    for (const item of continuing) {
                      const node = item.animation.effect && item.animation.effect.target;
                      if (!(node instanceof Element)) continue;
                      if (node === target || node.contains(target)) {
                        for (let current = target; current; current = current.parentElement) {
                          nodes.add(current);
                          if (current === node) break;
                        }
                      } else if (target.contains(node)) {
                        for (let current = node; current; current = current.parentElement) {
                          nodes.add(current);
                          if (current === target) break;
                        }
                      }
                    }
                    return Array.from(nodes);
                  })();
                  const snapshot = () => JSON.stringify(relatedNodes.map(node => {
                    const rect = node.getBoundingClientRect();
                    const style = getComputedStyle(node);
                    return {
                      rect: [rect.x, rect.y, rect.width, rect.height],
                      transform: style.transform,
                      opacity: style.opacity,
                      filter: style.filter,
                      boxShadow: style.boxShadow,
                      clipPath: style.clipPath,
                      visibility: style.visibility,
                      display: style.display
                    };
                  }));
                  for (const item of continuing) item.animation.currentTime = 0;
                  const baseline = snapshot();
                  let visible = 0;
                  for (const item of continuing) {
                    let changed = false;
                    for (const fraction of [0, .25, .5, .75, 1]) {
                      for (const candidate of continuing) candidate.animation.currentTime = 0;
                      item.animation.currentTime = item.probeTime * fraction;
                      if (snapshot() !== baseline) {
                        changed = true;
                        break;
                      }
                    }
                    if (changed) visible += 1;
                  }
                  for (const item of continuing) {
                    try { item.animation.currentTime = item.originalTime; } catch (_) {}
                    if (['running', 'pending'].includes(item.originalPlayState)) {
                      try { item.animation.play(); } catch (_) {}
                    }
                  }
                  return visible;
                }""",
                selector,
            )
        )
