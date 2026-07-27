/** 判断无语言代码块是否适合以紧凑单行样式呈现。 */
export function isCompactCodeBlock(content: string): boolean {
  return content.length <= 96 && !content.includes("\n");
}
