import { expect, test } from "@playwright/test";

for (const width of [390, 1280]) {
  test(`wrong answers enter personal review and retain their next date at ${width}px`, async ({ page }) => {
    await page.setViewportSize({ width, height: 844 });
    const quizId = "44444444-4444-4444-8444-444444444444";
    const attemptId = "55555555-5555-4555-8555-555555555555";
    const cardId = "66666666-6666-4666-8666-666666666666";
    const question = { id: "question", ordinal: 1, kind: "single", difficulty: "easy", topic: "统计", stem: "哪个统计量更不易受极端值影响？", options: ["均值", "中位数"], answer: "中位数", explanation: "中位数取排序后的中间位置。", sources: [] };
    let enrolled = false, rated = false, reveals = 0;
    const dueAt = "2026-10-01T00:00:00Z";
    await page.route("**/api/v1/auth/me", (route) => route.fulfill({ json: { user_id: "user", workspace_id: "workspace", email: "learner@example.test", csrf_token: "test" } }));
    await page.route("**/api/v1/quizzes/**", async (route) => {
      const path = new URL(route.request().url()).pathname;
      if (path.endsWith("/review-cards")) {
        enrolled = true; await route.fulfill({ json: { added: 1, existing: 0 } });
      } else if (path.endsWith(attemptId)) {
        await route.fulfill({ json: { id: attemptId, status: "submitted", score: 0, weak_topics: ["统计"], revision: 1, failure_code: null, questions: [{ ...question, earned: 0, response: "均值", feedback: "请复习", grading_method: "exact" }] } });
      } else {
        await route.fulfill({ json: { id: quizId, title: "统计练习", scope: [], status: "ready", questions: [] } });
      }
    });
    await page.route("**/api/v1/learning/review**", async (route) => {
      const path = new URL(route.request().url()).pathname;
      if (path.endsWith("/answer")) {
        reveals++; await route.fulfill({ json: question });
      } else if (path.endsWith(":rate")) {
        expect(route.request().postDataJSON()).toEqual({ rating: "good", expected_revision: 0 });
        expect(route.request().headers()["idempotency-key"]).toBeTruthy();
        rated = true; await route.fulfill({ json: { id: cardId, due_at: dueAt, interval_days: 1, revision: 1 } });
      } else {
        await route.fulfill({ json: { due_count: enrolled && !rated ? 1 : 0, upcoming_count: rated ? 1 : 0,
          items: enrolled && !rated ? [{ id: cardId, due_at: "2026-09-30T00:00:00Z", revision: 0, review_count: 0, question: { ...question, answer: null, explanation: null, sources: [] } }] : [],
          upcoming: rated ? [{ id: cardId, due_at: dueAt, topic: "统计", interval_days: 1 }] : [],
        } });
      }
    });
    await page.goto(`/quizzes/${quizId}/attempts/${attemptId}`);
    await page.getByRole("button", { name: "将错题加入长期复习" }).click();
    await page.getByRole("link", { name: "开始复习" }).click();
    await expect(page.getByRole("heading", { name: "长期复习" })).toBeVisible();
    expect(reveals).toBe(0);
    await expect(page.getByText(question.explanation)).toHaveCount(0);
    const reveal = page.getByRole("button", { name: "查看答案" });
    await reveal.focus(); await page.keyboard.press("Enter");
    await expect(page.getByText(question.explanation)).toBeVisible();
    const good = page.getByRole("button", { name: "记得", exact: true });
    await good.focus(); await page.keyboard.press("Enter");
    await expect(page.getByRole("heading", { name: "本轮复习已完成" })).toBeVisible();
    await expect(page.locator("time")).toHaveAttribute("datetime", dueAt);
    await page.reload();
    await expect(page.locator("time")).toHaveAttribute("datetime", dueAt);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  });
}
