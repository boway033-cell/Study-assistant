// 请求序号守卫：同一份状态只允许“最新一次请求”写回。
// 用法：发起请求前 token = next()；响应返回后只有 isCurrent(token) 为 true 才能写状态。
// 进入新范围时直接 next() 即可让旧请求全部失效；invalidate() 用于卸载/清空场景。
export function createSequencer() {
  let issued = 0
  let current = 0
  return {
    next() {
      issued += 1
      current = issued
      return current
    },
    isCurrent(token) {
      return token !== 0 && token === current
    },
    // 使所有已发出的 token 失效；之后仍可 next() 开启新一轮。
    invalidate() {
      current = 0
    },
  }
}
