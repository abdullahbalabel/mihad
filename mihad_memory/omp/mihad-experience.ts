/**
 * مهاد experience engine for OMP.
 *
 * Wires the Python engine (python -m mihad_memory.experience) into a live session:
 *   before_agent_start  brief: experience relevant to this task, sized by competence
 *   tool_result         live detection of known failure paths, repeated failures and re-reads,
 *                       and (when enabled) one consultation of a stronger model when the agent is stuck
 *   agent_end           experience review of the actual change; findings start one more turn
 *   tools               experience_review, experience_skill (verified skills compiled from past sessions)
 *
 * Two ways to configure it:
 *   1. Project mode (normal use): the project has .mihad/project.json, written by
 *      `python -m mihad_memory.install <project>`, which also registers this file in .omp/settings.json.
 *      The extension then also records each session for live learning (start/end snapshots), keeps the
 *      user's messages for the memory's preference capture, and starts a dream cycle every N sessions.
 *   2. Environment mode (practice sessions and research runs): MIHAD_EXPERIENCE_DIR and friends, set by the runner.
 *      MIHAD_EXPERIENCE_ROOT (mihad_memory package dir), _PY, _STATE, _MODEL, _EDGES, _ADVISOR.
 */
import * as fs from "node:fs";
import * as os from "node:os";
import * as path from "node:path";
import type { ExtensionAPI } from "@oh-my-pi/pi-coding-agent";

interface ProjectConfig {
	root: string;
	experience_dir?: string;
	python?: string;
	mihad_root?: string;
	edges?: boolean;
	advisor?: boolean;
	advisor_model?: string;
}

function findProjectConfig(start: string): ProjectConfig | undefined {
	let dir = path.resolve(start);
	for (;;) {
		const file = path.join(dir, ".mihad", "project.json");
		if (fs.existsSync(file)) {
			try {
				return { ...JSON.parse(fs.readFileSync(file, "utf8")), root: dir };
			} catch {
				return undefined;
			}
		}
		const parent = path.dirname(dir);
		if (parent === dir) return undefined;
		dir = parent;
	}
}

export default function (pi: ExtensionAPI) {
	const envMode = Boolean(process.env.MIHAD_EXPERIENCE_DIR);
	const cfg = envMode ? undefined : findProjectConfig(process.cwd());
	if (!envMode && !cfg) return;
	const live = !envMode; // project mode: record sessions, log user messages, auto-dream
	const dir = envMode
		? process.env.MIHAD_EXPERIENCE_DIR!
		: path.resolve(cfg!.root, cfg!.experience_dir || ".mihad/experience");
	const root = process.env.MIHAD_EXPERIENCE_ROOT || cfg?.mihad_root || process.cwd();
	const py = process.env.MIHAD_EXPERIENCE_PY || cfg?.python || "python";
	const edges = envMode ? Boolean(process.env.MIHAD_EXPERIENCE_EDGES) : cfg!.edges !== false;
	const state =
		process.env.MIHAD_EXPERIENCE_STATE ||
		path.join(envMode ? os.tmpdir() : path.join(dir, "state"), `session-${Date.now()}-${process.pid}.json`);
	const cache = `${state}.checkers.json`;
	const busy = `${state}.busy`;
	const extraEnv: Record<string, string> = {};
	if (!envMode && cfg!.advisor) extraEnv.MIHAD_EXPERIENCE_ADVISOR = "1";
	if (!envMode && cfg!.advisor_model) extraEnv.MIHAD_EXPERIENCE_ADVISOR_MODEL = cfg!.advisor_model;
	Object.assign(process.env, extraEnv); // inherited by the Python processes started below
	const z = pi.zod;
	let briefed = false;
	let reviewed = false;
	let reviewTurnPending = false;
	let started = false;

	async function engine(args: string[], timeout = 120_000): Promise<string> {
		const res = await pi.exec(py, ["-m", "mihad_memory.experience", "--dir", dir, ...args], { cwd: root, timeout });
		if (res.code !== 0) {
			pi.logger.debug("mihad experience failed", { args, code: res.code, stderr: res.stderr.slice(-500) });
			return "";
		}
		return res.stdout.trim();
	}

	function tmpFile(name: string, content: string): string {
		const file = `${state}.${name}`;
		fs.mkdirSync(path.dirname(file), { recursive: true });
		fs.writeFileSync(file, content, "utf8");
		return file;
	}

	function modelId(ctx: { model?: { id?: string; provider?: string } }): string | undefined {
		if (process.env.MIHAD_EXPERIENCE_MODEL) return process.env.MIHAD_EXPERIENCE_MODEL;
		const m = ctx.model;
		if (!m?.id) return undefined;
		return m.provider ? `${m.provider}/${m.id}` : m.id;
	}

	async function runReview(cwd: string): Promise<string> {
		const args = ["review", "--cwd", cwd, "--state", state, "--cache", cache];
		if (edges) args.push("--edges");
		return engine(args, 600_000);
	}

	pi.on("before_agent_start", async (event, ctx) => {
		const taskFile = tmpFile("task.txt", event.prompt);
		if (live) {
			if (reviewTurnPending) {
				reviewTurnPending = false; // the turn our own review started: not a new user request
				return;
			}
			reviewed = false; // a new request from the user gets its own review
			const sessionFile = ctx.sessionManager?.getSessionFile?.() ?? "";
			await engine(["live-prompt", "--cwd", ctx.cwd, "--task-file", taskFile, "--session-file", String(sessionFile),
				...(started ? [] : ["--first"])]);
			started = true;
		} else if (briefed) {
			return;
		}
		briefed = true;
		const args = ["brief", "--cwd", ctx.cwd, "--task-file", taskFile];
		const model = modelId(ctx);
		if (model) args.push("--model", model);
		if (live) args.push("--memory");
		const text = await engine(args);
		if (!text) return;
		return { message: { customType: "mihad-experience-brief", content: text, display: true } };
	});

	pi.on("tool_result", async (event, ctx) => {
		const text = event.content
			.map(c => (c.type === "text" ? c.text : ""))
			.join("")
			.slice(0, 4000);
		const file = tmpFile(
			"event.json",
			JSON.stringify({ toolName: event.toolName, input: event.input, isError: event.isError, text }),
		);
		// With the advisor on, detect may consult a stronger model once, so allow it time.
		const advice = await engine(["detect", "--event-file", file, "--state", state, "--cwd", ctx.cwd], 360_000);
		if (advice) return { additionalContext: advice };
	});

	pi.on("agent_end", async (event, ctx) => {
		if (event.willContinue) return;
		if (live) {
			const sessionFile = ctx.sessionManager?.getSessionFile?.() ?? "";
			await engine(["live-checkpoint", "--cwd", ctx.cwd, "--session-file", String(sessionFile), "--state", state]);
		}
		if (reviewed) return;
		reviewed = true;
		// Tell a runner a follow-up may come, so it does not close the session meanwhile.
		fs.mkdirSync(path.dirname(busy), { recursive: true });
		fs.writeFileSync(busy, String(Date.now()), "utf8");
		try {
			const text = await runReview(ctx.cwd);
			if (text) {
				reviewTurnPending = true;
				pi.sendMessage({ customType: "mihad-experience-review", content: text, display: true }, { triggerTurn: true });
			}
		} finally {
			setTimeout(() => fs.rmSync(busy, { force: true }), 5_000);
		}
	});

	pi.on("session_shutdown", async (_event, ctx) => {
		if (!live || !started) return;
		// Counts the session and starts a background dream cycle every N sessions (dream.auto_after_sessions).
		await engine(["live-close", "--cwd", ctx.cwd], 60_000);
	});

	pi.registerTool({
		name: "experience_review",
		label: "Experience review",
		description:
			"Check your current change against experience from past work on this project: checks from past fixes " +
			"(regressions), the user's co-change rules, stub and __all__ consistency, edge cases past fixes were " +
			"about, tests run after the last edit. Returns nothing to fix when the change is consistent.",
		parameters: z.object({}),
		loadMode: "essential",
		async execute(_id, _params, _signal, _onUpdate, ctx) {
			const text = await runReview(ctx.cwd);
			return { content: [{ type: "text", text: text || "Experience review: no findings." }] };
		},
	});

	pi.registerTool({
		name: "experience_skill",
		label: "Experience skill",
		description:
			"Run a verified skill compiled from past sessions on this project. " +
			"test_symbol (args: symbol=<function name>) runs the tests that exercise that function; " +
			"check_change runs focused tests for every function changed in git diff, then the full suite.",
		parameters: z.object({
			name: z.enum(["test_symbol", "check_change"]).describe("Skill name"),
			symbol: z.string().optional().describe("Function or class name, for test_symbol"),
		}),
		loadMode: "essential",
		async execute(_id, params, _signal, _onUpdate, ctx) {
			const args = ["skill", "run", params.name, "--cwd", ctx.cwd];
			if (params.symbol) args.push("--arg", `symbol=${params.symbol}`);
			const text = await engine(args, 900_000);
			return { content: [{ type: "text", text: text || "Skill failed to run." }] };
		},
	});
}
