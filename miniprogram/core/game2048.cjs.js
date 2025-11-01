// miniprogram/core/game2048.cjs.js // 定义 2048 核心逻辑（CJS 版），全部中文注释
const DEFAULT_WEIGHTS = { 1: 0.9, 2: 0.1 }; // 默认新方块概率权重：数值 1 占 90%，数值 2 占 10%
// ------------------------------
// 工具函数：根据权重随机抽取一个键值
function weightedPick(weights, randomFn) { // 接收权重对象与随机函数
  const entries = Object.entries(weights); // 将权重对象转换为键值对数组
  const total = entries.reduce((sum, [, w]) => sum + Number(w || 0), 0); // 累加得到总权重
  if (!total) { // 若总权重为 0
    return Number(entries[0] ? entries[0][0] : 1); // 回退返回第一个键或默认的 1
  } // if 分支结束
  const rand = (typeof randomFn === 'function' ? randomFn() : Math.random()) * total; // 生成 [0,total) 的随机数
  let acc = 0; // 累积权重
  for (const [value, weight] of entries) { // 遍历每个候选数值
    acc += Number(weight || 0); // 增加当前候选的权重
    if (rand < acc) { // 若随机数落在当前区间内
      return Number(value); // 返回该数值
    } // if 结束
  } // for 循环结束
  return Number(entries[entries.length - 1][0]); // 防御性返回最后一个键
} // weightedPick 函数结束
// ------------------------------
// Game2048 类：封装棋盘状态、移动逻辑与分数计算
class Game2048 { // 定义 2048 棋盘类，封装方块移动与分数统计
  constructor(options = {}) { // 构造函数，接收配置对象
    const { size = 4, randomTileWeights = DEFAULT_WEIGHTS, randomFn = Math.random } = options; // 解构配置并提供默认值
    this.size = size; // 保存棋盘尺寸
    this.randomTileWeights = { ...randomTileWeights }; // 拷贝权重配置，避免外部修改
    this.randomFn = randomFn; // 保存随机函数
    this.score = 0; // 当前分数
    this.maxTile = 0; // 当前最大方块数值
    this.grid = this._createEmptyGrid(); // 初始化空棋盘
    this._seedInitialTiles(); // 初始化时添加两个方块
  } // 构造函数结束
  // ------------------------------
  _createEmptyGrid() { // 创建空棋盘的内部工具函数
    const grid = []; // 定义结果数组
    for (let r = 0; r < this.size; r++) { // 遍历行
      const row = new Array(this.size).fill(0); // 创建一行全 0
      grid.push(row); // 将该行加入棋盘
    } // for 循环结束
    return grid; // 返回空棋盘
  } // _createEmptyGrid 方法结束
  // ------------------------------
  _seedInitialTiles() { // 初始化棋盘时添加两个起始方块
    this.score = 0; // 重置分数
    this.maxTile = 0; // 重置最大值
    this.grid = this._createEmptyGrid(); // 重新生成空棋盘
    this._addRandomTile(); // 添加第一个随机方块
    this._addRandomTile(); // 添加第二个随机方块
  } // _seedInitialTiles 方法结束
  // ------------------------------
  reset() { // 对外暴露的重置方法
    this._seedInitialTiles(); // 调用内部初始化逻辑
  } // reset 方法结束
  // ------------------------------
  getScore() { // 返回当前分数
    return this.score; // 直接返回分数字段
  } // getScore 方法结束
  // ------------------------------
  getMaxTile() { // 返回当前棋盘上的最大数值
    return this.maxTile; // 返回缓存的最大值
  } // getMaxTile 方法结束
  // ------------------------------
  getGrid() { // 获取棋盘状态的深拷贝
    return this.grid.map(row => row.slice()); // 对每一行进行浅拷贝，避免外部修改原始数组
  } // getGrid 方法结束
  // ------------------------------
  _emptyCells() { // 内部方法：获取所有空格坐标
    const cells = []; // 存放空格坐标
    for (let r = 0; r < this.size; r++) { // 遍历行
      for (let c = 0; c < this.size; c++) { // 遍历列
        if (this.grid[r][c] === 0) { // 判断当前格是否为空
          cells.push({ r, c }); // 记录空格坐标
        } // if 结束
      } // 内层 for 结束
    } // 外层 for 结束
    return cells; // 返回空格列表
  } // _emptyCells 方法结束
  // ------------------------------
  _addRandomTile() { // 内部方法：随机在空格中生成新方块
    const empties = this._emptyCells(); // 获取所有空格
    if (!empties.length) { // 若没有空格
      return false; // 返回 false 表示未添加方块
    } // if 结束
    const randIndex = Math.floor((typeof this.randomFn === 'function' ? this.randomFn() : Math.random()) * empties.length); // 随机选取一个空格索引
    const cell = empties[randIndex]; // 获取选中的空格
    const value = weightedPick(this.randomTileWeights, this.randomFn); // 根据权重随机生成方块数值
    this.grid[cell.r][cell.c] = value; // 将新方块放入棋盘
    if (value > this.maxTile) { // 若新方块超过当前最大值
      this.maxTile = value; // 更新最大方块
    } // if 结束
    return true; // 返回 true 表示成功添加
  } // _addRandomTile 方法结束
  // ------------------------------
  _compressLine(line) { // 内部方法：将一行非零数字向前压缩
    return line.filter(v => v !== 0); // 过滤掉 0，返回新的紧凑数组
  } // _compressLine 方法结束
  // ------------------------------
  _mergeLine(line) { // 内部方法：对压缩后的行执行合并
    const merged = []; // 存放合并后的结果
    let gained = 0; // 记录本行产生的分数
    for (let i = 0; i < line.length; i++) { // 遍历压缩后的行
      const current = line[i]; // 当前数值
      if (i + 1 < line.length && line[i + 1] === current) { // 若后一个元素存在且相等
        const newValue = current + line[i + 1]; // 合并后的数值为二者之和
        merged.push(newValue); // 将新值压入结果
        gained += newValue; // 累加得分
        if (newValue > this.maxTile) { // 若新值超过最大方块
          this.maxTile = newValue; // 更新最大方块缓存
        } // if 结束
        i++; // 跳过已合并的下一个元素
      } else { // 如果不能合并
        merged.push(current); // 直接保留当前值
        if (current > this.maxTile) { // 检查当前值是否刷新最大方块
          this.maxTile = current; // 更新最大方块缓存
        } // if 结束
      } // if-else 结束
    } // for 循环结束
    return { merged, gained }; // 返回合并结果与得分
  } // _mergeLine 方法结束
  // ------------------------------
  _padLine(line) { // 内部方法：将结果数组补齐为棋盘长度
    while (line.length < this.size) { // 只要长度不足
      line.push(0); // 在末尾补 0
    } // while 结束
    return line; // 返回补齐后的行
  } // _padLine 方法结束
  // ------------------------------
  _processLine(cells) { // 内部方法：根据提供的单元格坐标移动一整行或一整列
    const values = cells.map(({ r, c }) => this.grid[r][c]); // 抽取当前坐标上的值形成数组
    const compressed = this._compressLine(values); // 先移除 0
    const { merged, gained } = this._mergeLine(compressed); // 合并相邻相等的数字
    const filled = this._padLine(merged); // 将结果补齐长度
    let moved = false; // 标记是否发生移动或合并
    for (let i = 0; i < cells.length; i++) { // 遍历坐标列表
      const { r, c } = cells[i]; // 当前坐标
      if (this.grid[r][c] !== filled[i]) { // 若与原值不同
        moved = true; // 标记产生变化
        this.grid[r][c] = filled[i]; // 写回新值
      } // if 结束
    } // for 循环结束
    return { moved, gained }; // 返回是否移动与增加的分数
  } // _processLine 方法结束
  // ------------------------------
  move(direction) { // 执行一次移动（left/right/up/down）
    const vectors = { // 定义四个方向的遍历顺序
      left: { dr: 0, dc: 1, start: () => ({ r: 0, c: 0 }), outer: 'r' }, // 向左：行不变，列从左到右
      right: { dr: 0, dc: -1, start: () => ({ r: 0, c: this.size - 1 }), outer: 'r' }, // 向右：行不变，列从右到左
      up: { dr: 1, dc: 0, start: () => ({ r: 0, c: 0 }), outer: 'c' }, // 向上：列不变，行从上到下
      down: { dr: -1, dc: 0, start: () => ({ r: this.size - 1, c: 0 }), outer: 'c' } // 向下：列不变，行从下到上
    }; // vectors 定义结束
    const config = vectors[direction]; // 取得对应方向配置
    if (!config) { // 若方向非法
      return false; // 直接返回未移动
    } // if 结束
    let moved = false; // 记录整体是否移动
    let gainedTotal = 0; // 记录整体得分
    if (config.outer === 'r') { // 以行作为外层遍历
      for (let r = 0; r < this.size; r++) { // 遍历每一行
        const cells = []; // 准备该行的坐标序列
        const start = config.start(); // 计算起点
        for (let i = 0; i < this.size; i++) { // 遍历该行的每个格子
          const c = start.c + config.dc * i; // 根据方向计算列索引
          cells.push({ r, c }); // 加入坐标
        } // 内层 for 结束
        const result = this._processLine(cells); // 处理该行
        if (result.moved) { // 若有变化
          moved = true; // 标记整体发生移动
        } // if 结束
        gainedTotal += result.gained; // 累计得分
      } // 外层 for 结束
    } else { // 以列作为外层遍历
      for (let c = 0; c < this.size; c++) { // 遍历每一列
        const cells = []; // 准备该列的坐标序列
        const start = config.start(); // 计算起点
        for (let i = 0; i < this.size; i++) { // 遍历该列的每个格子
          const r = start.r + config.dr * i; // 根据方向计算行索引
          cells.push({ r, c }); // 加入坐标
        } // 内层 for 结束
        const result = this._processLine(cells); // 处理该列
        if (result.moved) { // 若有变化
          moved = true; // 标记整体发生移动
        } // if 结束
        gainedTotal += result.gained; // 累加得分
      } // 外层 for 结束
    } // if-else 结束
    if (!moved) { // 若没有任何变化
      return false; // 直接返回
    } // if 结束
    this.score += gainedTotal; // 累加本次移动的得分
    this._addRandomTile(); // 在空位生成新的随机方块
    return true; // 返回移动成功
  } // move 方法结束
  // ------------------------------
  canMove() { // 判断是否还能继续移动
    if (this._emptyCells().length > 0) { // 若存在空格
      return true; // 直接返回可移动
    } // if 结束
    for (let r = 0; r < this.size; r++) { // 遍历行
      for (let c = 0; c < this.size; c++) { // 遍历列
        const current = this.grid[r][c]; // 当前格数值
        const right = c + 1 < this.size ? this.grid[r][c + 1] : null; // 相邻右侧值
        const down = r + 1 < this.size ? this.grid[r + 1][c] : null; // 相邻下侧值
        if (right === current || down === current) { // 若存在与当前相等的邻居
          return true; // 仍可合并，返回可移动
        } // if 结束
      } // 内层 for 结束
    } // 外层 for 结束
    return false; // 无空格且无相等邻居，无法移动
  } // canMove 方法结束
} // Game2048 类定义结束
// ------------------------------
module.exports = { Game2048, DEFAULT_WEIGHTS }; // 导出 Game2048 类与默认权重配置
