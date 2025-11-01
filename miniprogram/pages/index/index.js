// /miniprogram/pages/index/index.js // 小程序首页逻辑，全部中文注释
// 说明：使用 createCanvasContext 绘制 2048 棋盘，并接入关卡管理器与触摸手势。
const { LevelManager } = require('../../core/levels.cjs.js');  // 引入关卡管理器（CJS）
const sys = wx.getSystemInfoSync();                            // 获取设备信息，用于计算画布尺寸
Page({ // 声明页面配置对象
  data: { // 页面绑定的数据对象
    canvasPx: 360,          // 画布目标像素尺寸（运行时计算覆盖）
    gap: 12,                // 格子间隙（像素）
    size: 2,                // 当前棋盘尺寸（由关卡决定）
    level: 1,               // 关卡号（2×2 视为第 1 关）
    target: 32,             // 当前关卡目标值
    score: 0,               // 当前分数
    totalScore: 0,          // 总分（含此前关卡累计）
    bestScore: 0            // 本地最佳分
  }, // data 定义结束
  onLoad() { // 生命周期：页面加载
    const best = Number(wx.getStorageSync('bestScore') || 0); // 读取本地最佳分（若没有则为 0）
    this.setData({ bestScore: best }); // 同步最佳分到页面数据
    this.LM = new LevelManager({ // 创建关卡管理器：从 2×2 起步，累计得分
      startSize: 2, // 起始尺寸为 2
      carryScore: true, // 启用跨关卡累计得分
      randomTileWeightsBySize: { 4: { 2: 0.9, 4: 0.1 } } // 可按尺寸覆盖新方块分布：示例为 4×4 起使用传统 2/4
    }); // LevelManager 初始化结束
    this.game = this.LM.getGame(); // 当前关卡的游戏实例
    const px = Math.min(Math.max(300, sys.windowWidth - 32), 480); // 预计算画布像素尺寸：贴合屏幕宽度并设上限
    this.setData({ canvasPx: Math.floor(px) }); // 将像素尺寸写入数据
  }, // onLoad 结束
  onReady() { // 生命周期：初次渲染完成
    this.ctx = wx.createCanvasContext('game', this); // 创建 2D 画布上下文对象
    this._computeTileSize(); // 按当前棋盘尺寸计算每格像素大小
    this._syncHud(); // 同步 HUD 信息到页面
    this._drawAll(); // 首次绘制棋盘
  }, // onReady 结束
  _computeTileSize() { // 计算每格像素大小：依据画布像素尺寸、格子间隙与棋盘尺寸
    const { canvasPx, gap } = this.data; // 从数据中读取画布尺寸与间隙
    const size = this.game.size; // 当前棋盘尺寸
    this.tileSize = (canvasPx - gap * (size + 1)) / size; // (总宽度 - 间隙总和) / 格子数
  }, // _computeTileSize 结束
  _roundRect(x, y, w, h, r, color) { // 绘制圆角矩形工具：使用二次贝塞尔曲线实现
    const ctx = this.ctx; // 获取画布上下文
    const rr = Math.min(r, w / 2, h / 2); // 防止圆角半径超过宽高
    ctx.beginPath(); // 开始路径
    ctx.moveTo(x + rr, y); // 移动到上边缘起点
    ctx.lineTo(x + w - rr, y); // 绘制上边直线
    ctx.quadraticCurveTo(x + w, y, x + w, y + rr); // 右上角圆弧
    ctx.lineTo(x + w, y + h - rr); // 绘制右边直线
    ctx.quadraticCurveTo(x + w, y + h, x + w - rr, y + h); // 右下角圆弧
    ctx.lineTo(x + rr, y + h); // 绘制下边直线
    ctx.quadraticCurveTo(x, y + h, x, y + h - rr); // 左下角圆弧
    ctx.lineTo(x, y + rr); // 绘制左边直线
    ctx.quadraticCurveTo(x, y, x + rr, y); // 左上角圆弧
    ctx.setFillStyle(color); // 设置填充颜色
    ctx.fill(); // 填充路径
    ctx.closePath(); // 关闭路径
  }, // _roundRect 结束
  _fitFont(text, maxW, maxH) { // 二分法拟合字号：仅以文本宽度为约束
    const ctx = this.ctx; // 获取画布上下文
    let lo = 4; // 字号下界
    let hi = Math.floor(maxH); // 字号上界（不超过内框高度）
    let best = lo; // 记录最佳字号
    while (lo <= hi) { // 二分循环
      const mid = Math.floor((lo + hi) / 2); // 取中间值
      ctx.setFontSize(mid); // 设置当前字号
      const w = ctx.measureText(text).width; // 测量文本宽度
      const h = mid; // 使用字号近似高度
      if (w <= maxW && h <= maxH) { // 若宽高均满足
        best = mid; // 更新最佳字号
        lo = mid + 1; // 继续尝试更大字号
      } else { // 若超出范围
        hi = mid - 1; // 缩小上界
      } // if-else 结束
    } // while 结束
    return best; // 返回最佳字号
  }, // _fitFont 结束
  _drawAll() { // 绘制整棋盘
    const ctx = this.ctx; // 获取画布上下文
    const gap = this.data.gap; // 格子间隙
    const size = this.game.size; // 棋盘尺寸
    const S = this.data.canvasPx; // 画布像素边长
    const T = this.tileSize; // 单格像素尺寸
    this._roundRect(0, 0, S, S, 10, '#bbada0'); // 绘制棋盘背板
    for (let r = 0; r < size; r++) { // 遍历行绘制空槽
      for (let c = 0; c < size; c++) { // 遍历列绘制空槽
        const x = gap + c * (T + gap); // 计算左上角 x 坐标
        const y = gap + r * (T + gap); // 计算左上角 y 坐标
        this._roundRect(x, y, T, T, 8, '#cdc1b4'); // 绘制空槽背景
      } // 列循环结束
    } // 行循环结束
    const COLORS = { // 具体数值的颜色映射（保持与 Web 版一致的主色）
      1:    { bg: '#eee4da', fg: '#776e65' }, // 数值 1 的颜色
      2:    { bg: '#ede0c8', fg: '#776e65' }, // 数值 2 的颜色
      4:    { bg: '#f2b179', fg: '#f9f6f2' }, // 数值 4 的颜色
      8:    { bg: '#f59563', fg: '#f9f6f2' }, // 数值 8 的颜色
      16:   { bg: '#f67c5f', fg: '#f9f6f2' }, // 数值 16 的颜色
      32:   { bg: '#f65e3b', fg: '#f9f6f2' }, // 数值 32 的颜色
      64:   { bg: '#edcf72', fg: '#f9f6f2' }, // 数值 64 的颜色
      128:  { bg: '#edcc61', fg: '#f9f6f2' }, // 数值 128 的颜色
      256:  { bg: '#edc850', fg: '#f9f6f2' }, // 数值 256 的颜色
      512:  { bg: '#edc53f', fg: '#f9f6f2' }, // 数值 512 的颜色
      1024: { bg: '#edc22e', fg: '#f9f6f2' }, // 数值 1024 的颜色
      2048: { bg: '#3c3a32', fg: '#f9f6f2' } // 数值 2048 的颜色
    }; // COLORS 定义结束
    const grid = this.game.getGrid(); // 获取当前棋盘状态
    for (let r = 0; r < size; r++) { // 遍历行渲染数字方块
      for (let c = 0; c < size; c++) { // 遍历列渲染数字方块
        const v = grid[r][c]; // 当前格数值
        if (!v) { // 若为空格
          continue; // 跳过绘制
        } // if 结束
        const x = gap + c * (T + gap); // 计算方块左上角 x
        const y = gap + r * (T + gap); // 计算方块左上角 y
        const sty = COLORS[v] || { bg: '#3c3a32', fg: '#f9f6f2' }; // 根据数值选择颜色，超出范围使用兜底颜色
        this._roundRect(x, y, T, T, 8, sty.bg); // 绘制背景方块
        const pad = Math.floor(T * 0.12); // 计算内边距
        const innerW = T - pad * 2; // 文字可用宽度
        const innerH = T - pad * 2; // 文字可用高度
        const text = String(v); // 数值转字符串
        const fontSize = this._fitFont(text, innerW, innerH); // 拟合最佳字号
        ctx.setFillStyle(sty.fg); // 设置文字颜色
        ctx.setFontSize(fontSize); // 设置文字字号
        ctx.setTextAlign('center'); // 水平居中对齐
        ctx.setTextBaseline('middle'); // 垂直居中对齐
        const cx = x + T / 2; // 计算文字中心 x
        const cy = y + T / 2; // 计算文字中心 y
        ctx.fillText(text, cx, cy); // 绘制文本
      } // 列循环结束
    } // 行循环结束
    const cur = this.game.getScore(); // 获取当前得分
    let best = this.data.bestScore; // 读取现有最佳分
    if (cur > best) { // 若当前得分超过最佳
      best = cur; // 更新最佳分缓存
      try { // 使用 try 捕捉可能的存储异常
        wx.setStorageSync('bestScore', String(best)); // 写入本地存储
      } catch (e) { // 捕获异常
        console.warn('无法写入最佳分', e); // 输出警告日志
      } // try-catch 结束
      this.setData({ bestScore: best }); // 同步最佳分到界面
    } // if 结束
    ctx.draw(); // 将缓冲区内容一次性提交到画布
    this.setData({ // 同步分数相关数据到 HUD
      score: cur, // 当前分数
      totalScore: this.LM.getTotalScore() // 累计分数
    }); // setData 调用结束
  }, // _drawAll 结束
  _syncHud() { // 同步关卡 HUD 文本
    this.setData({ // 批量更新关卡信息
      size: this.game.size, // 棋盘尺寸
      level: this.LM.getLevel(), // 关卡号
      target: this.LM.getTarget() // 当前目标值
    }); // setData 调用结束
  }, // _syncHud 结束
  onRestart() { // 重开本关
    this.game.reset(); // 重置当前游戏
    this._computeTileSize(); // 重新计算格子尺寸
    this._syncHud(); // 更新 HUD
    this._drawAll(); // 重绘棋盘
  }, // onRestart 结束
  _enterNextLevel() { // 进入下一关
    this.LM.nextLevel(); // 切换到下一关
    this.game = this.LM.getGame(); // 获取新的游戏实例
    this._computeTileSize(); // 根据新尺寸计算格子大小
    this._syncHud(); // 更新 HUD 文本
    this._drawAll(); // 重绘棋盘
  }, // _enterNextLevel 结束
  _doMove(dir) { // 单步移动与通关/死局判定
    const moved = this.game.move(dir); // 执行移动
    if (!moved) { // 若没有移动
      return; // 直接返回
    } // if 结束
    this._drawAll(); // 重绘棋盘
    if (this.LM.checkPass()) { // 通关判定
      wx.showModal({ // 弹出提示框
        title: '通关', // 标题
        content: '是否进入下一关', // 提示文案
        success: (res) => { // 处理用户响应
          if (res.confirm) { // 若用户确认
            this._enterNextLevel(); // 进入下一关
          } // if 结束
        } // success 回调结束
      }); // showModal 调用结束
      return; // 弹窗后不再继续检查死局
    } // if 结束
    if (!this.game.canMove()) { // 死局判定：无可移动步骤
      wx.showToast({ title: '无可用步', icon: 'none' }); // 弹出提示
    } // if 结束
  }, // _doMove 结束
  onTouchStart(e) { // 触摸起点记录
    const t = e.touches[0]; // 取首个触摸点
    this._t0 = { x: t.clientX, y: t.clientY }; // 缓存触摸起点坐标
  }, // onTouchStart 结束
  onTouchEnd(e) { // 触摸结束，计算滑动方向
    if (!this._t0) { // 若无起点记录
      return; // 直接返回
    } // if 结束
    const t = e.changedTouches[0]; // 获取结束触摸点
    const dx = t.clientX - this._t0.x; // 计算横向位移
    const dy = t.clientY - this._t0.y; // 计算纵向位移
    const ax = Math.abs(dx); // 横向绝对值
    const ay = Math.abs(dy); // 纵向绝对值
    const min = 20; // 最小滑动阈值
    if (ax < min && ay < min) { // 位移不足阈值
      return; // 忽略本次滑动
    } // if 结束
    if (ax > ay) { // 横向位移更大
      this._doMove(dx > 0 ? 'right' : 'left'); // 判断左右方向执行移动
    } else { // 纵向位移更大
      this._doMove(dy > 0 ? 'down' : 'up'); // 判断上下方向执行移动
    } // if-else 结束
    this._t0 = null; // 清空起点记录
  }, // onTouchEnd 结束
  onResize() { // 适配窗口尺寸变化（开发者工具里有效）
    const px = Math.min(Math.max(300, sys.windowWidth - 32), 480); // 重新计算画布像素尺寸
    this.setData({ canvasPx: Math.floor(px) }, () => { // 更新画布尺寸后回调
      this._computeTileSize(); // 回调中重新计算格子尺寸
      this._drawAll(); // 回调中重绘棋盘
    }); // setData 调用结束
  } // onResize 结束
}); // Page 定义结束
