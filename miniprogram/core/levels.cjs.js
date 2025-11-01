// miniprogram/core/levels.cjs.js // 定义关卡管理器，中文注释覆盖每行
const { Game2048, DEFAULT_WEIGHTS } = require('./game2048.cjs.js'); // 引入 2048 核心逻辑与默认权重
// ------------------------------
class LevelManager { // LevelManager 负责管理关卡尺寸、目标与累计得分
  constructor(options = {}) { // 构造函数接收可选配置
    const { startSize = 2, targetFn = (size) => 2 ** (size + 3), carryScore = false, randomTileWeightsBySize = {}, baseRandomTileWeights = DEFAULT_WEIGHTS } = options; // 解构配置并提供默认值
    this.startSize = startSize; // 保存起始尺寸
    this.targetFn = targetFn; // 保存目标函数
    this.carryScore = carryScore; // 保存是否累积分数
    this.randomTileWeightsBySize = { ...randomTileWeightsBySize }; // 拷贝权重映射
    this.baseRandomTileWeights = { ...baseRandomTileWeights }; // 拷贝基础权重
    this.levelIndex = 0; // 当前关卡索引（0 表示第一关）
    this.totalScore = 0; // 记录跨关卡累计得分
    this.game = null; // 当前关卡对应的 Game2048 实例
    this._createGameForCurrentLevel(); // 初始化第一关的游戏实例
  } // 构造函数结束
  // ------------------------------
  _currentSize() { // 内部方法：计算当前关卡棋盘尺寸
    return this.startSize + this.levelIndex; // 每升一级尺寸加 1
  } // _currentSize 方法结束
  // ------------------------------
  _currentWeights() { // 内部方法：获取当前关卡的新方块权重
    const size = this._currentSize(); // 计算当前尺寸
    return this.randomTileWeightsBySize[size] || this.baseRandomTileWeights; // 优先取自定义权重，否则使用基础权重
  } // _currentWeights 方法结束
  // ------------------------------
  _createGameForCurrentLevel() { // 内部方法：根据当前关卡创建游戏实例
    const size = this._currentSize(); // 获取当前棋盘尺寸
    const weights = this._currentWeights(); // 获取当前权重
    this.game = new Game2048({ size, randomTileWeights: weights }); // 创建新的 Game2048 实例
  } // _createGameForCurrentLevel 方法结束
  // ------------------------------
  getGame() { // 返回当前 Game2048 实例
    return this.game; // 直接返回实例
  } // getGame 方法结束
  // ------------------------------
  getLevel() { // 获取当前关卡编号
    return this.levelIndex + 1; // 将索引转换为从 1 开始的关卡号
  } // getLevel 方法结束
  // ------------------------------
  getTarget() { // 获取当前关卡目标值
    return this.targetFn(this._currentSize()); // 调用目标函数计算目标值
  } // getTarget 方法结束
  // ------------------------------
  getTotalScore() { // 获取累计得分
    if (this.carryScore) { // 若开启累积分数
      return this.totalScore + (this.game ? this.game.getScore() : 0); // 累加历史得分与当前关卡分数
    } // if 结束
    return this.game ? this.game.getScore() : 0; // 未开启累积则仅返回当前关卡分
  } // getTotalScore 方法结束
  // ------------------------------
  checkPass() { // 判断是否达到通关条件
    if (!this.game) { // 若游戏实例不存在
      return false; // 直接返回未通关
    } // if 结束
    return this.game.getMaxTile() >= this.getTarget(); // 最大方块达到或超过目标即通关
  } // checkPass 方法结束
  // ------------------------------
  nextLevel() { // 进入下一关
    if (this.game && this.carryScore) { // 若存在当前游戏且需要累积分数
      this.totalScore += this.game.getScore(); // 将当前关卡分数计入累计得分
    } // if 结束
    this.levelIndex += 1; // 关卡索引加一
    this._createGameForCurrentLevel(); // 创建新关卡的游戏实例
  } // nextLevel 方法结束
} // LevelManager 类定义结束
// ------------------------------
module.exports = { LevelManager }; // 导出 LevelManager 供小程序端调用
