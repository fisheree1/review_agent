import { expect, test } from "@playwright/test";

test("complete Quiz, review mistakes and draft weak practice within the conversation", async ({ page }) => {
  const documentId = "11111111-1111-4111-8111-111111111111";
  const conversationId = "33333333-3333-4333-8333-333333333333";
  const quizId = "44444444-4444-4444-8444-444444444444";
  const attemptId = "55555555-5555-4555-8555-555555555555";
  const questionId = "66666666-6666-4666-8666-666666666666";
  const scope = [{ document_id: documentId, version_id: 1, filename: "stats.pdf" }];
  const conversation = { id: conversationId, title: "统计复习", scope, created_at: "2026-09-23T00:00:00Z" };
  const messages: Record<string, unknown>[] = [];
  let status = "in_progress";
  let response: string | null = null;
  let started = false;
  const quiz = { id: quizId, title: "统计练习", scope, status: "ready", question_count: 1,
    config: { type_counts: { single: 1, multiple: 0, true_false: 0, short: 0 }, difficulty: "medium", language: "zh", topic: "统计", generation_mode: "agent" } };

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
      const { question } = route.request().postDataJSON() as { question: string };
      const review = question.includes("解释");
      const weak = question.includes("薄弱");
      const message = { id: `77777777-7777-4777-8777-${String(messages.length + 1).padStart(12, "0")}`,
        question, scope, status: "answered", answer: null, feedback: null, failure_message: null,
        task_result: { kind: review ? "review" : "quiz", title: review ? "错题复习" : weak ? "薄弱点练习" : "统计练习",
          text: review ? "以下是当前范围最近一次练习的错题与来源。" : "已根据当前资料准备练习。",
          quiz_id: quizId, attempt_id: review ? attemptId : null } };
      messages.push(message); data = message;
    }
    await route.fulfill({ json: data });
  });
  await page.route("**/api/v1/quizzes**", async (route) => {
    const path = new URL(route.request().url()).pathname;
    let data: unknown = quiz;
    if (path.endsWith("/attempts")) {
      if (route.request().method() === "POST") { started = true; data = { id: attemptId, status }; }
      else data = started ? [{ id: attemptId, status }] : [];
    } else if (path.includes("/answers/")) {
      response = (route.request().postDataJSON() as { response: string }).response;
      data = { question_id: questionId, response };
    } else if (path.endsWith(":submit")) {
      status = "submitted"; data = { id: attemptId, status, score: 0, weak_topics: ["统计"] };
    } else if (path.endsWith(attemptId)) {
      const complete = status === "submitted";
      data = { id: attemptId, status, score: complete ? 0 : null, weak_topics: complete ? ["统计"] : null,
        questions: [{ id: questionId, ordinal: 1, kind: "single", difficulty: "medium", topic: "统计",
          stem: "哪个统计量不易受极端值影响？", options: ["中位数", "均值", "众数"], response,
          answer: complete ? "中位数" : null, earned: complete ? 0 : null,
          feedback: complete ? "建议核对统计量的定义。" : null, grading_method: complete ? "automatic" : null,
          explanation: complete ? "原文说明中位数不易受极端值影响。" : null,
          sources: complete ? [{ source_id: "source", document_id: documentId, version_id: 1, unit: 1,
            locator: { kind: "page", position: 1, title: null, path: [] }, quote: "The median resists extreme outliers." }] : [] }] };
    }
    await route.fulfill({ json: data });
  });

  await page.goto(`/study/${conversationId}`);
  await expect(page.getByText("当前范围：stats.pdf")).toBeVisible();
  const composer = page.getByLabel("发送任务或问题");
  await composer.fill("生成一道关于统计的单选题。");
  await composer.press("Control+Enter");
  const practice = page.getByRole("region", { name: "练习任务结果" });
  await expect(practice).toBeVisible();
  await practice.getByRole("button", { name: "在对话中作答" }).click();
  await expect(practice.getByText("参考答案：中位数")).not.toBeVisible();
  await practice.getByRole("radio", { name: "均值", exact: true }).check();
  await practice.getByRole("button", { name: "提交作答" }).click();
  await expect(practice.getByText("得分 0 / 100")).toBeVisible();
  await expect(practice.getByText("参考答案：中位数")).toBeVisible();

  await composer.fill("解释我最近练习的错题。");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  const review = page.getByRole("region", { name: "错题复习结果" });
  await expect(review.getByText("你的答案：均值")).toBeVisible();
  await expect(review.getByRole("link", { name: "第 1 页 · 返回原文" })).toHaveAttribute("href", `/documents/${documentId}?unit=1&version=1`);
  await review.getByRole("button", { name: "练习薄弱知识点" }).click();
  await expect(composer).toHaveValue(/薄弱知识点/);
  expect(messages).toHaveLength(2);
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.getByRole("heading", { name: "薄弱点练习", exact: true })).toBeVisible();
  await expect(page).toHaveURL(`/study/${conversationId}`);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  const ids = await page.locator("[id]").evaluateAll((elements) => elements.map((element) => element.id));
  expect(new Set(ids).size).toBe(ids.length);
  await page.screenshot({ path: "/private/tmp/review-agent-conversation-mobile.png", fullPage: true });
});
