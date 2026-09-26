import { expect, test } from "@playwright/test";

test("switch document scope, rate a cited answer, and complete an Agent Quiz", async ({ page }) => {
  const first = "11111111-1111-4111-8111-111111111111";
  const second = "22222222-2222-4222-8222-222222222222";
  const conversationId = "33333333-3333-4333-8333-333333333333";
  const quizId = "44444444-4444-4444-8444-444444444444";
  const attemptId = "55555555-5555-4555-8555-555555555555";
  const questionId = "66666666-6666-4666-8666-666666666666";
  const documents = [first, second].map((id, index) => ({
    id, filename: index === 0 ? "stats.pdf" : "classes.pdf", media_type: "application/pdf",
    status: "ready", content_count: 1, page_count: 1, byte_size: 100,
    created_at: "2026-09-23T00:00:00Z", updated_at: "2026-09-23T00:00:00Z",
    failure_code: null, failure_message: null,
  }));
  const locator = { kind: "page", position: 1, title: null, path: [] };
  let conversation: Record<string, unknown> | null = null;
  let messages: Record<string, unknown>[] = [];
  let rating: string | null = null;
  let quiz: Record<string, unknown> | null = null;
  let attemptStatus = "in_progress";
  let response: string | null = null;

  await page.route("**/api/v1/auth/me", (route) => route.fulfill({ json: {
    user_id: "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
    workspace_id: "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb",
    email: "learner@example.test", csrf_token: "csrf-e2e",
  } }));

  await page.route("**/api/v1/documents**", (route) => route.fulfill({ json: { items: documents, next_cursor: null } }));
  await page.route("**/api/v1/collections**", (route) => route.fulfill({ json: [] }));
  await page.route("**/api/v1/conversations**", async (route) => {
    const url = new URL(route.request().url());
    let data: unknown;
    if (url.pathname.endsWith("/conversations")) {
      if (route.request().method() === "POST") {
        const body = route.request().postDataJSON() as { document_ids: string[]; title: string };
        conversation = { id: conversationId, title: body.title, scope: body.document_ids.map((id) => ({ document_id: id, version_id: 1, filename: documents.find((doc) => doc.id === id)?.filename })), created_at: "2026-09-23T00:00:00Z" };
        data = conversation;
      } else data = conversation ? [conversation] : [];
    } else if (url.pathname.endsWith("/scope")) {
      const body = route.request().postDataJSON() as { document_ids: string[] };
      conversation = { ...conversation, scope: body.document_ids.map((id) => ({ document_id: id, version_id: 1, filename: documents.find((doc) => doc.id === id)?.filename })) };
      data = conversation;
    } else if (url.pathname.endsWith("/feedback")) {
      rating = (route.request().postDataJSON() as { rating: string }).rating;
      data = { rating };
    } else if (url.pathname.endsWith("/messages")) {
      const body = route.request().postDataJSON() as { question: string };
      const message = { id: "77777777-7777-4777-8777-777777777777", question: body.question,
        scope: conversation?.scope, status: "answered", failure_message: null, feedback: null,
        answer: { insufficient_evidence: false, claims: [{ text: "A class initializes an object.", citations: [{ source_id: "source", document_id: second, version_id: 2, unit: 1, locator, quote: "A class initializes an object with __init__." }] }] } };
      messages = [message]; data = message;
    } else {
      data = { ...conversation, messages: messages.map((message) => ({ ...message, feedback: rating })) };
    }
    await route.fulfill({ json: data });
  });
  await page.route("**/api/v1/quizzes**", async (route) => {
    const url = new URL(route.request().url());
    let data: unknown;
    if (url.pathname.endsWith("/quizzes")) {
      if (route.request().method() === "POST") {
        const body = route.request().postDataJSON() as { title: string; config: unknown };
        expect((body.config as { generation_mode: string }).generation_mode).toBe("agent");
        quiz = { id: quizId, title: body.title, config: body.config, status: "ready", question_count: 1,
          scope: [{ document_id: first, version_id: 1, filename: "stats.pdf" }], failure_message: null };
        data = quiz;
      } else data = quiz ? [quiz] : [];
    } else if (url.pathname.endsWith("/attempts")) {
      if (route.request().method() === "POST") data = { id: attemptId, status: "in_progress" };
      else data = [];
    } else if (url.pathname.endsWith(":submit")) {
      attemptStatus = "submitted";
      data = { id: attemptId, status: "submitted", score: 100, weak_topics: [] };
    } else if (url.pathname.includes("/answers/")) {
      response = (route.request().postDataJSON() as { response: string }).response;
      data = { question_id: questionId, response };
    } else if (url.pathname.endsWith(attemptId)) {
      data = { id: attemptId, status: attemptStatus, score: attemptStatus === "submitted" ? 100 : null,
        weak_topics: attemptStatus === "submitted" ? [] : null, failure_code: null,
        questions: [{ id: questionId, ordinal: 1, kind: "single", difficulty: "medium", topic: "statistics",
          stem: "Which statistic resists outliers?", options: ["Median", "Mean", "Mode"], response,
          answer: attemptStatus === "submitted" ? "Median" : null,
          explanation: attemptStatus === "submitted" ? "The source identifies the median." : null,
          sources: attemptStatus === "submitted" ? [{ source_id: "source", document_id: first, version_id: 1, unit: 1, locator, quote: "The median resists extreme outliers." }] : [],
          earned: attemptStatus === "submitted" ? 1 : null, feedback: attemptStatus === "submitted" ? "正确" : null,
          grading_method: attemptStatus === "submitted" ? "automatic" : null }] };
    } else data = { ...quiz, questions: [{ id: questionId, ordinal: 1, kind: "single", difficulty: "medium", topic: "statistics", stem: "Which statistic resists outliers?", options: ["Median", "Mean", "Mode"], answer: null, explanation: null, sources: [] }] };
    await route.fulfill({ json: data });
  });

  await page.goto("/study");
  await page.getByLabel("对话标题").fill("Course review");
  await page.getByRole("group", { name: "选择资料范围" }).getByLabel("stats.pdf").check();
  await page.getByRole("group", { name: "选择资料范围" }).getByLabel("classes.pdf").check();
  await page.getByRole("button", { name: "创建对话" }).click();
  await expect(page.getByText("当前范围：stats.pdf、classes.pdf")).toBeVisible();
  await page.getByText("调整资料范围", { exact: true }).click();
  await page.getByRole("group", { name: "切换后续提问范围" }).getByLabel("stats.pdf").uncheck();
  await page.getByRole("button", { name: "保存新范围" }).click();
  await expect(page.getByText("当前范围：classes.pdf")).toBeVisible();
  await page.getByLabel("发送任务或问题").fill("Why use the median?");
  await page.getByRole("button", { name: "发送", exact: true }).click();
  await expect(page.getByText("A class initializes an object.")).toBeVisible();
  await page.getByRole("button", { name: "有帮助" }).click();
  await expect(page.getByText("已反馈：有帮助")).toBeVisible();

  await page.goto("/quizzes");
  await page.getByLabel("标题", { exact: true }).fill("Statistics check");
  await page.getByRole("group", { name: "出题资料范围" }).getByLabel("stats.pdf").check();
  await expect(page.getByLabel("出题方式")).toHaveValue("standard");
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByLabel("出题方式").selectOption("agent");
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await page.getByRole("button", { name: /生成 5 题/ }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByText("已生成 1 题")).toBeVisible();
  await expect(page.getByText("出题方式：自主规划出题（实验）")).toBeVisible();
  await page.getByRole("button", { name: "开始作答" }).click();
  await expect(page.getByText("参考答案：Median")).not.toBeVisible();
  await page.getByRole("radio", { name: "Median" }).check();
  await page.getByRole("button", { name: "提交作答" }).click();
  await expect(page.getByText("得分 100 / 100")).toBeVisible();
  await expect(page.getByText("参考答案：Median")).toBeVisible();
  await expect(page.getByRole("link", { name: "第 1 页 · 返回原文" })).toHaveAttribute("href", `/documents/${first}?unit=1&version=1`);
  await page.setViewportSize({ width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});
