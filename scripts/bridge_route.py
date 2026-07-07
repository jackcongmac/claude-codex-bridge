#!/usr/bin/env python3
"""Recommend Claude/Codex heavy-role routing from normalized usage readings."""

import argparse
import datetime as _dt
import json
import os
import time

import bridge_usage


DEFAULT_CONFIG = {
    "HEAVY_5H": 80,
    "HEAVY_WEEK": 85,
    "STALE_AFTER": 600,
    "DEFAULT_IMPL": "Codex",
}

ENV_KEYS = {
    "HEAVY_5H": "BRIDGE_ROUTE_HEAVY_5H",
    "HEAVY_WEEK": "BRIDGE_ROUTE_HEAVY_WEEK",
    "STALE_AFTER": "BRIDGE_ROUTE_STALE_AFTER",
    "DEFAULT_IMPL": "BRIDGE_ROUTE_DEFAULT_IMPL",
}

MODELS = ("Claude", "Codex")


def _model_name(value):
    if value is None:
        return None
    text = str(value).strip().lower()
    if text == "claude":
        return "Claude"
    if text == "codex":
        return "Codex"
    return None


def _int_setting(value, fallback):
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _resolve_config(config=None, env=None):
    env = os.environ if env is None else env
    resolved = dict(DEFAULT_CONFIG)
    for key, env_key in ENV_KEYS.items():
        if env_key not in env:
            continue
        if key == "DEFAULT_IMPL":
            resolved[key] = _model_name(env[env_key]) or resolved[key]
        else:
            resolved[key] = _int_setting(env[env_key], resolved[key])
    if config:
        for key, value in config.items():
            if key == "DEFAULT_IMPL":
                resolved[key] = _model_name(value) or resolved[key]
            elif key in resolved:
                resolved[key] = _int_setting(value, resolved[key])
    return resolved


def _pct(value):
    if value is None:
        return None
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def _timestamp(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            text = value[:-1] + "+00:00" if value.endswith("Z") else value
            try:
                return _dt.datetime.fromisoformat(text).timestamp()
            except ValueError:
                return None
    return None


def _reset_in_s(values, now_ts):
    reset_times = []
    for value in values:
        ts = _timestamp(value)
        if ts is not None:
            reset_times.append(ts)
    if not reset_times:
        return None
    return max(0, int(round(min(reset_times) - float(now_ts))))


def _freshness(model, usage, now_ts, stale_after):
    if usage is None:
        return True, None
    ts_key = "mtime" if model == "Claude" else "event_ts"
    ts = _timestamp(usage.get(ts_key))
    if ts is None:
        return True, None
    age = float(now_ts) - ts
    return age > stale_after, age


def _signal(model, usage, now_ts, cfg):
    if model == "Claude":
        five_h = _pct(usage.get("five_hour_pct")) if usage else None
        weekly = _pct(usage.get("seven_day_pct")) if usage else None
        reset_values = (
            usage.get("five_hour_reset") if usage else None,
            usage.get("seven_day_reset") if usage else None,
        )
    else:
        five_h = _pct(usage.get("primary_pct")) if usage else None
        weekly = _pct(usage.get("secondary_pct")) if usage else None
        reset_values = (
            usage.get("primary_reset") if usage else None,
            usage.get("secondary_reset") if usage else None,
        )
    stale, age = _freshness(model, usage, now_ts, cfg["STALE_AFTER"])
    pressured = (
        (five_h is not None and five_h >= cfg["HEAVY_5H"])
        or (weekly is not None and weekly >= cfg["HEAVY_WEEK"])
    )
    return {
        "five_h": five_h,
        "weekly": weekly,
        "reset_in_s": _reset_in_s(reset_values, now_ts),
        "stale": stale,
        "pressured": pressured,
        "_missing": usage is None,
        "_age": age,
    }


def _public_signal(signal):
    return {
        "five_h": signal["five_h"],
        "weekly": signal["weekly"],
        "reset_in_s": signal["reset_in_s"],
        "stale": signal["stale"],
        "pressured": signal["pressured"],
    }


def _other(model):
    if model == "Claude":
        return "Codex"
    if model == "Codex":
        return "Claude"
    return None


def _max_pressure(signal):
    values = [value for value in (signal["five_h"], signal["weekly"]) if value is not None]
    return max(values) if values else 1000


def _fresh_low(signal):
    return not signal["stale"] and not signal["pressured"]


def _pressure_reason(model, signal, cfg):
    if signal["five_h"] is not None and signal["five_h"] >= cfg["HEAVY_5H"]:
        return "%s 5h %s%% >=%s%%" % (model, signal["five_h"], cfg["HEAVY_5H"])
    if signal["weekly"] is not None and signal["weekly"] >= cfg["HEAVY_WEEK"]:
        return "%s weekly %s%% >=%s%%" % (model, signal["weekly"], cfg["HEAVY_WEEK"])
    return "%s quota pressure" % model


def _stale_warning(model, signal):
    if signal["_missing"]:
        return "%s reading missing; quota unknown" % model
    age = signal["_age"]
    if age is None:
        return "%s reading timestamp missing; quota unknown" % model
    minutes = max(1, int(age // 60))
    return "%s reading %sm stale; quota unknown" % (model, minutes)


def _choose_default(candidates, default_impl):
    if default_impl in candidates:
        return default_impl
    return candidates[0] if candidates else default_impl


def _best_effort(signals, default_impl):
    ordered = sorted(MODELS, key=lambda model: (_max_pressure(signals[model]), model != default_impl))
    return ordered[0] if ordered else None


def _decision(signals, cfg, current):
    current_signal = signals.get(current) if current else None
    if current_signal is not None and _fresh_low(current_signal):
        return (
            current,
            "Both models below heavy thresholds; keep current %s to avoid thrash" % current,
        )

    fresh_low_models = [model for model in MODELS if _fresh_low(signals[model])]
    if len(fresh_low_models) == 1:
        chosen = fresh_low_models[0]
        other = _other(chosen)
        if current and other == current and signals[other]["pressured"]:
            reason = "%s -> switch heavy work to %s" % (
                _pressure_reason(other, signals[other], cfg),
                chosen,
            )
        elif signals[other]["pressured"]:
            reason = "%s -> hand heavy work to %s" % (
                _pressure_reason(other, signals[other], cfg),
                chosen,
            )
        elif signals[other]["stale"]:
            reason = "%s has fresh low quota; %s quota is unknown" % (chosen, other)
        else:
            reason = "%s has the only fresh low quota reading" % chosen
        return chosen, reason

    if len(fresh_low_models) == 2:
        chosen = _choose_default(fresh_low_models, cfg["DEFAULT_IMPL"])
        return (
            chosen,
            "Both models fresh below heavy thresholds; default implementer %s" % chosen,
        )

    if all(signals[model]["pressured"] for model in MODELS):
        chosen = _best_effort(signals, cfg["DEFAULT_IMPL"])
        other = _other(chosen)
        return (
            chosen,
            "Both models pressured; %s max %s%% <= %s max %s%%" % (
                chosen,
                _max_pressure(signals[chosen]),
                other,
                _max_pressure(signals[other]),
            ),
        )

    chosen = _best_effort(signals, cfg["DEFAULT_IMPL"])
    return chosen, "Quota data is incomplete; best-effort implementer %s" % chosen


def recommend(claude, codex, *, now_ts, current=None, config=None):
    """Return an advisory route recommendation without side effects."""
    cfg = _resolve_config(config)
    current = _model_name(current)
    signals = {
        "Claude": _signal("Claude", claude, now_ts, cfg),
        "Codex": _signal("Codex", codex, now_ts, cfg),
    }
    implementer, reason = _decision(signals, cfg, current)
    reviewer = _other(implementer)

    warnings = []
    for model in MODELS:
        if signals[model]["stale"]:
            warnings.append(_stale_warning(model, signals[model]))
    if all(signals[model]["pressured"] for model in MODELS):
        warnings.append("both models under quota pressure")

    confidence = "low" if warnings else "high"
    if implementer and signals[implementer]["stale"]:
        confidence = "low"
    return {
        "implementer": implementer,
        "reviewer": reviewer,
        "reason": reason,
        "confidence": confidence,
        "warnings": warnings,
        "signals": {
            "Claude": _public_signal(signals["Claude"]),
            "Codex": _public_signal(signals["Codex"]),
        },
    }


def _now_from_args(args, env):
    value = args.now_ts if args.now_ts is not None else env.get("BRIDGE_ROUTE_NOW_TS")
    if value is not None:
        return float(value)
    return time.time()


def _format_human(result):
    line = "ROUTE: implement->%s, review->%s  (%s)  [%s]" % (
        result["implementer"] or "-",
        result["reviewer"] or "-",
        result["reason"],
        result["confidence"],
    )
    if result["warnings"]:
        line += "  WARN " + "; ".join(result["warnings"])
    return line


def main(argv=None):
    parser = argparse.ArgumentParser(description="Recommend Claude/Codex heavy-role routing.")
    parser.add_argument("--current", help="current implementer, Claude or Codex")
    parser.add_argument("--json", action="store_true", help="print full recommendation JSON")
    parser.add_argument("--now-ts", type=float, help="override current unix time")
    args = parser.parse_args(argv)

    now_ts = _now_from_args(args, os.environ)
    result = recommend(
        bridge_usage.read_claude(),
        bridge_usage.read_codex(),
        now_ts=now_ts,
        current=args.current,
    )
    if args.json:
        print(json.dumps(result, sort_keys=True))
    else:
        print(_format_human(result))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
