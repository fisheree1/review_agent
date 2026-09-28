import { expect, test } from "@playwright/test";

test("study workflow restores drafts, allows waiting questions and completes review on mobile", async ({ page }) => {
  const documentId = "11111111-1111-4111-8111-111111111111";
  const conversationId = "33333333-3333-4333-8333-333333333333";
  const quizId = "44444444-4444-4444-8444-444444444444";
  const attemptId = "55555555-5555-4555-8555-555555555555";
  const questionId = "66666666-6666-4666-8666-666666666666";
  const runId = "77777777-7777-4777-8777-777777777777";
  const scope = [{ document_id: documentId, version_id: 1, filename: "stats.pdf" }];
  const conversation = { id: conversationId, title: "统计学习", scope, created_at: "2026-09-27T00:00:00Z" };
  const messages: Record<string, unknown>[] = [];
  let response: string | null = null;
  let revision = 0;
  let submitted = false;
  const quizOutput = { kind: "quiz", quiz_id: quizId, attempt_id: attemptId, title: "章节练习", text: "已生成 1 / 1 题。" };

  await page.route("**/api/v1/conversation-groups", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/v1/auth/me", (route) => route.fulfill({ json: {
    user_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", workspace_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    email: "learner@example.test", csrf_token: "csrf-e2e",
  } }));
  await page.route("**/api/v1/documents**", (route) => route.fulfill({ json: { items: [{
    id: documentId, filename: "stats.pdf", media_type: "application/pdf", status: "ready", content_count: 1,
    page_count: 1, byte_size: 100, created_at: conversation.created_at, updated_at: conversation.created_at,
    failure_code: null, failure_message: null,
  }], next_cursor: null } }));
  await page.route("**/api/v1/collections**", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/v1/conversations**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let data: unknown = { ...conversation, messages };
    if (path.endsWith("/conversations")) data = [conversation];
    if (path.endsWith("/messages")) {
      const { question } = route.request().postDataJSON();
      const workflow = messages.length === 0;
      const message = { id: `message-${messages.length + 1}`, question, scope, status: "answered",
        run_id: workflow ? runId : null, answer: workflow ? { insufficient_evidence: false,
          claims: [{ text: "章节总结：中位数不易受极端值影响。", citations: [{ source_id: "source", document_id: documentId, version_id: 1, ordinal: 1, unit: 1, locator: { kind: "page", position: 1, title: null, path: [] }, quote: "中位数不易受极端值影响。" }] }] } : null,
        feedback: null, failure_message: null,
        task_result: workflow ? null : { kind: "clarification", text: "追加问题已接收，等待任务保留原范围。" } };
      messages.push(message); data = message;
    }
    await route.fulfill({ json: data });
  });
  await page.route("**/api/v1/agent-runs/**", (route) => route.fulfill({ json: {
    id: runId, scope, revision: submitted ? 6 : 3, graph_version: "study-graph-v1",
    status: submitted ? "completed" : "waiting_input", stage: submitted ? "done" : "wait",
    plan: { action: "study", review_after_submit: true, practice_after_review: true },
    outputs: [{ kind: "summary", text: "章节总结已完成" }, quizOutput,
      ...(submitted ? [{ kind: "review", quiz_id: quizId, attempt_id: attemptId, text: "这是本次实际作答的错题解析。" },
        { kind: "quiz", quiz_id: "weak-quiz", attempt_id: "weak-attempt", title: "薄弱点练习", text: "已生成 1 / 1 题。" }] : [])],
    clarification: null, failure_message: null, expires_at: "2026-10-04T00:00:00Z",
  } }));
  await page.route("**/api/v1/quizzes/**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    if (path.includes("/answers/")) {
      const body = route.request().postDataJSON();
      expect(body.expected_revision).toBe(revision);
      response = body.response;
      await route.fulfill({ json: { question_id: questionId, response, revision: ++revision } });
    } else if (path.endsWith(":submit")) {
      submitted = true;
      await route.fulfill({ json: { id: attemptId, status: "submitted", score: 0, weak_topics: ["统计"] } });
    } else {
      const weak = path.includes("weak-quiz");
      const complete = !weak && submitted;
      await route.fulfill({ json: { id: weak ? "weak-attempt" : attemptId,
        status: complete ? "submitted" : "in_progress", revision, score: complete ? 0 : null,
        weak_topics: complete ? ["统计"] : null, failure_code: null, questions: [{
          id: weak ? "weak-question" : questionId, ordinal: 1, kind: "single", difficulty: "medium", topic: "统计",
          stem: weak ? "再判断一次：哪个统计量较稳健？" : "哪个统计量不易受极端值影响？", options: ["中位数", "均值"],
          response: weak ? null : response, answer: complete ? "中位数" : null, earned: complete ? 0 : null,
          feedback: complete ? "请核对资料中的定义。" : null, grading_method: complete ? "automatic" : null,
          explanation: complete ? "中位数不易受极端值影响。" : null, sources: complete ? [{ source_id: "source",
            document_id: documentId, version_id: 1, unit: 1, locator: { kind: "page", position: 1, path: [] },
            quote: "The median resists extreme outliers." }] : [],
        }] } });
    }
  });

  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(`/study/${conversationId}`);
  await page.getByRole("button", { name: "完整学习", exact: true }).click();
  await page.getByLabel("发送任务或问题").press("Control+Enter");
  const card = page.getByRole("region", { name: "学习任务进度" });
  await expect(card.getByText("等待你作答", { exact: true })).toBeVisible();
  await expect(page.getByText("章节总结：中位数不易受极端值影响。")).toBeVisible();
  await expect(card.getByText(/参考答案/)).not.toBeVisible();
  await card.getByRole("radio", { name: "均值", exact: true }).check();
  await expect(card.getByText("作答已保存")).toBeVisible();
  await page.reload();
  await expect(card.getByRole("radio", { name: "均值", exact: true })).toBeChecked();
  const composer = page.getByLabel("发送任务或问题");
  await composer.fill("刚才的概念是什么意思？");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.getByText("追加问题已接收，等待任务保留原范围。")).toBeVisible();
  await expect(card.getByText("等待你作答", { exact: true })).toBeVisible();
  await card.getByRole("button", { name: "提交作答" }).click();
  const review = card.getByRole("region", { name: "任务错题复习" });
  await expect(review.getByText("你的答案：均值")).toBeVisible();
  await expect(review.getByText("参考答案：中位数")).toBeVisible();
  await expect(card.getByText("再判断一次：哪个统计量较稳健？")).toBeVisible();
  await page.reload();
  await expect(card.getByText("已完成", { exact: true })).toBeVisible();
  expect(messages).toHaveLength(2);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  const ids = await page.locator("[id]").evaluateAll((elements) => elements.map((element) => element.id));
  expect(new Set(ids).size).toBe(ids.length);
  await page.screenshot({ path: "/private/tmp/review-agent-workflow-mobile.png", fullPage: true });
});
