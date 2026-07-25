type FollowLatestInput = {
  nearBottom: boolean;
  latestRole: "user" | "assistant" | undefined;
  forced: boolean;
};

/** 判断新内容到达后是否应将视口跟随到最新位置。 */
export function shouldFollowLatest({ nearBottom, latestRole, forced }: FollowLatestInput): boolean {
  return forced || nearBottom || latestRole === "user";
}
