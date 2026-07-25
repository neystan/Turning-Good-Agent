type SearchableSession = {
  title: string;
  updated_at: string;
};

/** 按标题筛选会话，并将最近更新的结果放在前面。 */
export function filterSessions<T extends SearchableSession>(sessions: T[], query: string): T[] {
  const keyword = query.trim().toLocaleLowerCase();
  return sessions
    .filter((session) => !keyword || session.title.toLocaleLowerCase().includes(keyword))
    .sort((left, right) => right.updated_at.localeCompare(left.updated_at));
}
