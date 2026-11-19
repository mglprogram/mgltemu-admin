<!--
  头程跟踪（以入库单号 / 帕沃尔参考单号为主键 · 含到港→上架预测）

  2026-11-02 改版（用户要求）
  --------------------------
  「其他单号改成入库单号，因为可以通过领星查，所以直接从领星每天抓取入库单号跟踪数据更全」
  「根据到港日期和签收日期帮我预测到港之后，平均几天能签收送仓上架」

  2026-11-05（用户要求）
  ----------------------
  「超过三天上架的单号和 OMS 已取消的删除，之后超过三天已上架的在定时更新货件跟踪时去清理」
  （原文照录；页面显示名 2026-11-09 已改为「头程跟踪」）
  ⇒ 后端 prune_rows() 每次抓取就清掉；前端「已上架」视图窗口从 30 天改成 3 天。

  2026-11-18（用户要求）
  ----------------------
  「点击一次单号即可复制」
  「物流最新节点：点击这个单号内容即可看到整个上次爬取的整个轨迹，但不展开默认显示的还是
    上次爬取的最新一条轨迹」
  「把现在的轨迹改成查最新轨迹，即带运单号 LH 或者 IB 跳转到乐航轨迹链接或者恒信轨迹链接」
  ⇒ ① 复制单号：先按用户「点击一次即可复制」改成 click，当天又要求「改回双击复制单号」⇒ 回到 dblclick；
     ② 「物流最新节点」整格可点，弹抽屉看全量轨迹（原「轨迹」按钮取消）；
     ③ 操作列改成外链「查最新轨迹」：LH → 乐航，IB → 恒信；
     ④ 「预测上架」列改成**这票到港之后才显示**预测上架日（没到港写「未到港」，超期列跟着空）。

  所以这一页现在是：
    · 主键 = LM739 领星入库单号 IB106… / 14450 帕沃尔参考单号 14450…，每天全量拉
    · 到港 = 恒信【到港】节点（只覆盖 LM739；帕沃尔没有到港节点）
    · 预测：LM739 = 到港日 + 渠道中位天数；帕沃尔 = 下单日 + 渠道总天数（以星35/美森25/盐田45）
    · 超期 = 今天已超过预测上架日、还没到「已上架」
    · 只留在途与待催的货：已上架满 3 天、已取消的每次抓取自动清掉

  数据来自 public/data/shipment-track.json（scripts/goods/fetch_shipment_track.py）。
-->
<template>
  <div class="sh-page app-container">
    <!-- 页头 -->
    <div class="sh-header">
      <div>
        <h2 class="sh-header__title">头程跟踪 · {{ data.summary.total }} 票</h2>
        <p class="sh-header__desc">
          <b>LM739</b> 走领星 OMS 入库单号（IB106…），到港取恒信【到港】节点；
          <b>14450</b> 的领星已停用，改从<b>帕沃尔头程</b>抓（参考单号 14450…，含完整轨迹与预计到货）。
          <b>预测上架</b>两个账号口径不同：
          <b>乐航（14450）</b>只看<b>周几到港、与渠道无关</b>（周一到 → 下周二；周二到 → 下周五；周三～周日到 → 下周六）；
          <b>恒信（LM739）</b>按<b>渠道</b>（美森 周一～周三到当周周六、周四～周日到下周六；以星 到港 +13 天；盐田 到港 +14 天）。
          被<b>美国海关</b>查验的上架日整体后移两周；出口/国内查验不算（船期已含）。
          超过预测日还没上架的单独挑出来。
        </p>
      </div>
      <div class="sh-header__meta">
        <el-tag v-if="data.generatedAt" type="info" effect="plain" size="small">
          抓取 {{ data.generatedAt }}
        </el-tag>
        <el-button :loading="loading" @click="load">
          <el-icon><Refresh /></el-icon><span>刷新</span>
        </el-button>
      </div>
    </div>

    <!-- 预测口径 -->
    <el-alert type="info" :closable="false" show-icon class="sh-alert" title="到港 → 送仓上架 的预测口径">
      <div class="sh-alert__body">
        <div>
          <b>乐航（14450 帕沃尔）</b>：只看<b>周几到港，跟渠道无关</b>（用户 2026-11-19 定稿）——
          <span class="sh-alert__ch">周一到 <b>下周二</b></span>
          <span class="sh-alert__ch">周二到 <b>下周五</b></span>
          <span class="sh-alert__ch">周三～周日到 <b>下周六</b></span>
          <div class="sh-dim">
            例：11-15（周日）→ 11-21；11-16（周一）→ 11-24；11-17（周二）→ 11-27；11-18（周三）→ 11-28。
            美森 / 以星 / 盐田同一到港日给同一个上架日。
          </div>
          <br />
          <b>恒信（LM739）</b>：按<b>渠道</b>，从<b>到港日</b>起算 ——
          <span class="sh-alert__ch">美森 <b>当周周六</b>（周一～周三到）/ 下周六</span>
          <span class="sh-alert__ch">以星 <b>+13 天</b></span>
          <span class="sh-alert__ch">盐田 <b>+14 天</b></span>
          <div class="sh-dim">
            美森船期稳定时周一到港当周就能上架；以星到港后约 3 个工作日提柜 + 拆柜上架，
            含周末合计约 13 个自然日；盐田 13~15 天，取中位 14。
          </div>
        </div>
        <div>
          <b>被查验</b>：只有<b>美国海关（国外）查验</b>才调整 ——
          正常上架日<b>整体后移 {{ INSPECTION_DAYS }} 天</b>；若已放行且放行日更晚，
          再按周顺延到不早于放行日的那一班。
          <div class="sh-dim">
            出口查验 / 国内查验发生在开船前，船期（ETA）已经含了这段延误，<b>不再加两周</b>。
          </div>
        </div>
      </div>
    </el-alert>

    <!-- 合计 -->
    <el-row :gutter="12" class="sh-stats">
      <el-col v-for="s in stats" :key="s.label" :xs="12" :sm="8" :md="6" :lg="3">
        <el-card shadow="never" :body-style="{ padding: '12px 14px' }" class="sh-stat">
          <div class="sh-stat__label">{{ s.label }}</div>
          <div class="sh-stat__value" :style="{ color: s.color }">{{ s.value }}</div>
          <div class="sh-stat__hint">{{ s.hint }}</div>
        </el-card>
      </el-col>
    </el-row>

    <!-- 筛选 -->
    <div class="sh-bar">
      <el-radio-group v-model="viewPick" size="default">
        <el-radio-button value="all">全部</el-radio-button>
        <el-radio-button value="overdue">超期未上架</el-radio-button>
        <el-radio-button value="arrived">已到港待上架</el-radio-button>
        <el-radio-button value="waiting">未到港</el-radio-button>
        <!-- 近 3 天已上架（2026-11-05 用户要求：满 3 天的在定时抓取时清掉） -->
        <el-radio-button value="shelved">已上架</el-radio-button>
      </el-radio-group>
      <el-select v-model="accountPick" size="default" style="width: 170px">
        <el-option label="全部账号/仓库" value="all" />
        <el-option v-for="a in accountOpts" :key="a.value" :label="a.label" :value="a.value" />
      </el-select>
      <el-select v-model="chPick" size="default" style="width: 150px">
        <el-option label="全部渠道" value="all" />
        <el-option v-for="(n, k) in data.summary.byChannel" :key="k" :label="`${k} (${n})`" :value="k" />
      </el-select>
      <el-select v-model="stPick" size="default" style="width: 140px">
        <el-option label="全部 OMS 状态" value="all" />
        <el-option v-for="(n, k) in data.summary.byStatus" :key="k" :label="`${k} (${n})`" :value="k" />
      </el-select>
      <el-input
        v-model="keyword"
        size="default"
        clearable
        placeholder="搜 入库单号 / 帕沃尔单号 / 乐航号 / 产品名 / SKU / 款号"
        style="width: 260px"
      >
        <template #prefix><el-icon><Search /></el-icon></template>
      </el-input>
      <el-switch v-model="onlyIssue" active-text="只看需关注" size="default" />
      <el-button size="default" plain @click="copyTodo">复制清单</el-button>
    </div>

    <!-- 主体表 -->
    <el-card shadow="never" class="sh-card">
      <template #header>
        <div class="sh-card__head">
          <span class="sh-card__title">
            {{ viewTitle }} · {{ filtered.length }} 张
            <span class="sh-card__sub">
              （展开行看产品·尺码·数量；双击单号即复制；点「物流最新节点」看整条轨迹；
              点「查最新轨迹」去货代官网查最新的）
            </span>
          </span>
          <span class="sh-hint">
            <template v-if="viewPick === 'shelved'">
              近 {{ SHELVED_DAYS }} 天已上架 <b>{{ filtered.length }}</b> 票
            </template>
            <template v-else>
              已到港未上架 <b>{{ filteredArrivedWaiting }}</b> ｜ 超期 <b>{{ filteredOverdue }}</b>
            </template>
          </span>
        </div>
      </template>

      <el-table
        :data="paged"
        size="small"
        stripe
        border
        row-key="inboundNo"
        :row-class-name="rowClass"
        class="sh-table"
      >
        <!-- 展开：产品明细 -->
        <el-table-column type="expand">
          <template #default="{ row }">
            <div class="sh-exp">
              <div class="sh-exp__head">
                <b>{{ row.inboundNo }}</b>
                <el-tag :type="omsTag(row.status)" size="small" effect="light">{{ row.statusText }}</el-tag>
                <span class="sh-dim">
                  {{ row.whName }} ｜ {{ row.skuCount }} 个 SKU ｜ 共
                  <b>{{ num(row.totalQty) }}</b> 件
                  <template v-if="row.omsAccount"> ｜ 账号 {{ row.account }}</template>
                  <template v-if="row.expectedDate"> ｜ OMS 预计到货 {{ row.expectedDate }}</template>
              <span v-if="row.arriveVoided" class="sh-warn">
                ｜ ⚠ 预报到港后出现海关查验，此前的预计到港已作废，等货代更新船期
              </span>
                  <template v-if="row.receivedTime"> ｜ 收货 {{ row.receivedTime.slice(0, 10) }}</template>
                  <template v-if="row.shelfTime"> ｜ 上架 {{ row.shelfTime.slice(0, 10) }}</template>
                </span>
              </div>

              <el-table v-if="row.skus.length" :data="row.skus" size="small" border class="sh-exp__t">
                <el-table-column prop="productName" label="产品名称" min-width="230" />
                <el-table-column prop="style" label="款号 / 套系" min-width="180" />
                <el-table-column prop="size" label="尺码" width="80" align="center" />
                <el-table-column prop="qty" label="数量" width="90" align="right">
                  <template #default="{ row: s }"><b>{{ num(s.qty) }}</b></template>
                </el-table-column>
                <el-table-column prop="receivedQty" label="已收货" width="90" align="right">
                  <template #default="{ row: s }">{{ num(s.receivedQty) }}</template>
                </el-table-column>
                <el-table-column prop="sku" label="SKU" min-width="150" />
              </el-table>
              <el-empty v-else description="这张入库单没有 SKU 明细" :image-size="50" />
            </div>
          </template>
        </el-table-column>

        <el-table-column prop="inboundNo" label="入库单号" width="134" fixed>
          <template #default="{ row }">
            <!-- 帕沃尔那批：上面一行是**乐航运单号**（LH10059114），下面才是帕沃尔参考单号。
                 用户 2026-11-13 给的效果图就是这个顺序：乐航号在上、帕沃尔号在下。
                 乐航号装了同批次的多张帕沃尔单（顿号分隔），所以下面那行可能不止一个号。
                 ★ 2026-11-17 用户要求「双击时复制恒信的单号或者乐航的单号」⇒
                   两行各自复制**自己那个号**。
                 ★ 2026-11-18 一度改成单击，用户当天又要求「改回双击复制单号」⇒ 恢复 dblclick
                   （单击太容易误触，看表时顺手一点就把剪贴板冲掉了）。 -->
            <template v-if="row.source === '帕沃尔头程'">
              <div
                v-if="lhNo(row)"
                class="sh-lh sh-copyable"
                :title="`双击复制乐航号：${lhNo(row)}`"
                @dblclick.stop="copyNo(lhNo(row), '乐航运单号')"
              >
                {{ lhNo(row) }}
              </div>
              <div
                class="sh-mono sh-dim sh-tiny sh-copyable"
                :title="`双击复制帕沃尔号：${row.inboundNo}`"
                @dblclick.stop="copyNo(row.inboundNo, '帕沃尔参考单号')"
              >
                {{ row.inboundNo }}
              </div>
            </template>
            <span
              v-else
              class="sh-mono sh-copyable"
              :title="`双击复制恒信入库单号：${row.inboundNo}`"
              @dblclick.stop="copyNo(row.inboundNo, '恒信入库单号')"
            >{{ row.inboundNo }}</span>
          </template>
        </el-table-column>
        <el-table-column label="OMS" width="84" align="center" fixed>
          <template #default="{ row }">
            <el-tag :type="omsTag(row.status)" size="small" effect="light">{{ row.statusText }}</el-tag>
            <!-- 帕沃尔：上架看的是**箱数**（已入箱数 = 总箱数 ⇒ 已上架），
                 用户 2026-11-05 给的口径，比状态文字细 -->
            <div v-if="row.boxes != null" class="sh-dim sh-tiny" :title="`已入箱 ${row.inBoxes} / ${row.boxes} 箱`">
              {{ row.inBoxes }}/{{ row.boxes }} 箱
            </div>
          </template>
        </el-table-column>
        <el-table-column label="到港日期" width="100" align="center">
          <template #default="{ row }">
            <template v-if="row.arriveDate">
              <span>{{ row.arriveDate }}</span>
              <!-- 预报到港（「预计11/29到港」）与真的到港要能分开看，
                   否则会拿一个预报日期去算「已等几天」 -->
              <div v-if="isForecast(row)" class="sh-dim sh-tiny" title="货代轨迹里只写了「预计到港」，不是实际到港">
                预计
              </div>
            </template>
            <!-- 2026-11-19 用户口径：预报之后出现（国内/国外）海关查验 ⇒ 那条预报作废，
                 清空到港日期，等货代给出新的离港/到港时间。 -->
            <span v-else-if="row.arriveVoided" class="sh-dim sh-tiny" title="预报到港后出现海关查验，船期已变，此前的预计到港不再准确，等货代更新">
              查验·待更新
            </span>
            <span v-else class="sh-dim">—</span>
          </template>
        </el-table-column>
        <el-table-column label="到港→上架 / 已等" width="86" align="right">
          <template #default="{ row }">
            <!-- 已上架的给「到港→上架 实际用了几天」；没上架才是「已经等了几天」。
                 ⚠ 2026-11-17 用户反馈：乐航那批还没到港（到港日只是「预计11/23」），
                 这列却显示 10 天，像"到了 10 天还没上架"。
                 现在后端只在**真实到港**时给 waitDays，预报到港的一律 null ⇒ 这里走「—」分支。 -->
            <template v-if="row.status === 4 && shelfDays(asRow(row)) != null">
              <span :class="shelfDays(asRow(row))! <= 8 ? 'sh-ok' : 'sh-warn'">{{ shelfDays(asRow(row)) }} 天</span>
              <div class="sh-dim sh-tiny">到港→上架</div>
            </template>
            <template v-else-if="row.waitDays != null">
              <span :class="{ 'sh-bad': row.overdueDays > 0 }">{{ row.waitDays }} 天</span>
              <div class="sh-dim sh-tiny">已等</div>
            </template>
            <!-- 还没到港：明确说"未到港"，不要拿一个数字让人误以为在等上架 -->
            <span v-else-if="isForecast(row)" class="sh-dim" title="货代只给了预计到港日，还没真正到港，不计算等待天数">
              未到港
            </span>
            <span v-else-if="row.arriveVoided" class="sh-dim" title="预报到港后出现海关查验，等货代更新船期，暂不计算">
              待更新
            </span>
            <span v-else class="sh-dim">—</span>
          </template>
        </el-table-column>
        <el-table-column label="预测 / 上架" width="104" align="center">
          <template #default="{ row }">
            <!-- 已经上架的就直接给**实际**上架日期，别再显示预测（那已经是过去式了） -->
            <template v-if="row.status === 4 && row.shelfTime">
              <div class="sh-ok">{{ String(row.shelfTime).slice(0, 10) }}</div>
              <div class="sh-dim sh-tiny">实际上架</div>
            </template>
            <!-- 2026-11-19 口径：乐航按到港星期、恒信按渠道；被美国海关查验的后推两周。
                 到港后才显示；悬浮看 basis（含哪个渠道 / 是否查验 / 从哪天起算）。 -->
            <template v-else-if="row.predictedShelf && hasArrived(row)">
              <div>{{ row.predictedShelf }}</div>
              <div class="sh-dim sh-tiny" :title="row.predictedBasis">
                {{ isPw(row) ? "大概送仓" : "预测上架" }}
              </div>
            </template>
            <!-- 预报被查验作废：预测上架也一并清空，别给一个基于旧船期的日期 -->
            <span
              v-else-if="row.arriveVoided"
              class="sh-dim sh-tiny"
              title="预报到港后出现海关查验，船期已变，等货代更新到港时间后再算"
            >
              待更新
            </span>
            <span
              v-else-if="row.predictedShelf"
              class="sh-dim sh-tiny"
              title="这票还没真正到港（预报到港不算），到港后才给预测"
            >
              未到港
            </span>
            <span v-else class="sh-dim">—</span>
          </template>
        </el-table-column>
        <el-table-column label="超期" width="70" align="center">
          <template #default="{ row }">
            <el-tag v-if="row.overdueDays > 0" type="danger" size="small" effect="dark">
              +{{ row.overdueDays }} 天
            </el-tag>
            <!-- 超期也只对真的到港了的单有意义；没到港就空着 -->
            <span v-else-if="row.predictedShelf && row.status !== 4 && hasArrived(row)" class="sh-dim">未超</span>
            <span v-else class="sh-dim">—</span>
          </template>
        </el-table-column>
        <el-table-column label="渠道" width="80">
          <template #default="{ row }">
            <span :class="row.channel ? '' : 'sh-dim'">{{ row.channelText }}</span>
          </template>
        </el-table-column>
        <el-table-column label="物流最新节点" min-width="180">
          <template #default="{ row }">
            <template v-if="row.logistics.ok">
              <!-- 2026-11-18 用户要求：这一格平时还是只显示「上次爬取的最新一条节点」，
                   但**点一下节点内容**就弹出抽屉看整条轨迹（和原来点「轨迹」按钮一个效果）。 -->
              <div
                class="sh-node sh-node--open"
                :class="{ 'is-bad': row.logistics.currentAbnormal }"
                :title="`点开看上次爬取的完整轨迹（${row.logistics.traceCount} 个节点）`"
                @click.stop="openTraces(asRow(row))"
              >
                {{ row.logistics.latest.info || "—" }}
              </div>
              <div class="sh-node__time">
                {{ row.logistics.latest.time }}
                <!-- 帕沃尔那批的轨迹来自乐航（货代全链路），标一下免得以为还是帕沃尔三条 -->
                <span v-if="row.logistics.traceSource === '乐航'" class="sh-src">乐航</span>
              </div>
            </template>
            <!-- 14450 已停用领星（2026-11-02），改从帕沃尔头程 + 乐航货代轨迹取 -->
            <span v-else class="sh-dim">
              {{ row.source === "帕沃尔头程" ? "帕沃尔/乐航都无轨迹" : "恒信无此单" }}
            </span>
          </template>
        </el-table-column>
        <el-table-column label="产品 · 尺码 · 数量" min-width="160">
          <template #default="{ row }">
            <template v-if="row.skus.length">
              <span v-for="s in row.skus" :key="s.sku" class="sh-sku">
                {{ s.productName }} <b>×{{ num(s.qty) }}</b>
              </span>
            </template>
            <span v-else class="sh-dim">—</span>
          </template>
        </el-table-column>
        <el-table-column prop="whName" label="仓库" width="100" show-overflow-tooltip />
        <el-table-column label="需关注" width="110">
          <template #default="{ row }">
            <div v-if="row.issues.length" class="sh-issues">
              <el-tooltip
                v-for="i in row.issues"
                :key="i"
                :content="i"
                placement="top"
                :show-after="300"
              >
                <el-tag :type="issueTag(i)" size="small" effect="plain" class="sh-issue">
                  {{ i }}
                </el-tag>
              </el-tooltip>
            </div>
            <span v-else class="sh-dim">—</span>
          </template>
        </el-table-column>
        <el-table-column label="操作" width="92" align="center">
          <template #default="{ row }">
            <!-- 2026-11-18 用户要求：原来的「轨迹」抽屉按钮改成**查最新轨迹**，
                 按单号前缀直接跳货代官网：
                   乐航（LH…）→ lehang.nextsls.com/tracking/app#/tracking?numbers=LH…
                   恒信（IB…）→ hengxe.nextsls.com/tracking/app#/tracking?numbers=IB…
                 站内轨迹只到上次抓取，要看最新的就点这里出去查。 -->
            <el-link
              v-if="latestTraceUrl(row)"
              type="primary"
              :underline="false"
              :href="latestTraceUrl(row)"
              target="_blank"
              rel="noopener"
              style="font-size: 12px"
              :title="`用 ${traceNoOf(row)} 去查最新轨迹`"
            >
              查最新轨迹
            </el-link>
            <span v-else class="sh-dim">—</span>
          </template>
        </el-table-column>
      </el-table>

      <div class="sh-pagebar">
        <!-- ⚠ 2026-11-19 修：原来写的是 `:page-size="pageSize"`（单向绑定）+ `@size-change`
             里只重置页码，**pageSize 从来没被改过** —— 选「100 条/页」看着下拉变了，
             表格还是 50 行。必须用 `v-model:page-size` 双向绑定。 -->
        <el-pagination
          v-model:current-page="page"
          v-model:page-size="pageSize"
          :total="filtered.length"
          :page-sizes="[20, 50, 100, 200]"
          layout="total, sizes, prev, pager, next, jumper"
          background
          @size-change="onSizeChange"
        />
      </div>
    </el-card>

    <!-- 全链路轨迹 -->
    <el-drawer v-model="traceOpen" :title="`物流轨迹 · ${traceRow?.inboundNo || ''}`" size="560px">
      <template v-if="traceRow">
        <div class="sh-drawer__meta">
          <el-tag :type="logTag(traceRow.logistics.status)" size="small" effect="light">
            {{ traceRow.logistics.statusText || "无轨迹" }}
          </el-tag>
          <el-tag v-if="traceRow.logistics.traceSource" type="info" size="small" effect="plain">
            {{ traceRow.logistics.traceSource }}
          </el-tag>
          <span class="sh-dim">
            <template v-if="traceRow.logistics.shipmentId">单号 {{ traceRow.logistics.shipmentId }} ｜ </template>
            <template v-if="traceRow.logistics.parcels != null">{{ traceRow.logistics.parcels }} 件 ｜ </template>
            到港 {{ traceRow.arriveDate || "—" }}
            <template v-if="traceRow.predictedShelf"> ｜ 预测上架 {{ traceRow.predictedShelf }}</template>
          </span>
        </div>
        <!-- 说清这只是「上次爬取」的结果，别拿它当实时轨迹 -->
        <p class="sh-dim sh-drawer__note">
          以下是<b>上次爬取</b>（{{ data.generatedAt || "未知时间" }}）的整条轨迹
          {{ traceRow.logistics.traces.length }} 个节点；要看最新的点操作列「查最新轨迹」去货代官网。
        </p>
        <el-timeline v-if="traceRow.logistics.traces.length" class="sh-timeline">
          <el-timeline-item
            v-for="(t, i) in traceRow.logistics.traces"
            :key="i"
            :timestamp="t.time"
            :type="i === 0 ? 'primary' : ''"
            :hollow="i !== 0"
            placement="top"
          >
            <span :class="{ 'sh-bad': i === 0 && traceRow.logistics.currentAbnormal }">{{ t.info }}</span>
          </el-timeline-item>
        </el-timeline>
        <el-empty v-else description="货代那边查不到这票的轨迹" :image-size="60" />
      </template>
    </el-drawer>

    <!-- 口径 -->
    <el-card shadow="never" class="sh-card sh-rule">
      <template #header>
        <span class="sh-card__title">口径与数据源</span>
      </template>
      <div class="sh-rule__body">
        <p>
          <b>主键</b>：LM739（ZOO WAREHOUSE ⑥美西）用领星入库单号 <code>IB106…</code>；
          14450 的领星已停用，改用<b>帕沃尔参考单号</b> <code>14450…</code>。每天定时全量拉。
        </p>
        <p>
          <b>这一页怎么点</b>：单号<b>双击就复制</b>（乐航号 / 帕沃尔号 / 入库单号各复制自己）；
          <b>物流最新节点</b>平时只显示上次爬取的最新一条，<b>点一下就展开整条轨迹</b>（时间倒序）；
          要看<b>实时</b>轨迹点操作列的<b>查最新轨迹</b> —— 乐航号（<code>LH…</code>）跳乐航查件页，
          入库单号（<code>IB…</code>）跳恒信查件页。
        </p>
        <p>
          <b>到港</b>：恒信轨迹里的 <code>【到港】</code> 节点（LM739）/ 乐航轨迹里的「已到港」（帕沃尔），
          都带真实到港时间；「预计…到港」只算预报，不当作已到港。
        </p>
        <p>
          <b>预测上架 / 大概送仓</b>：<b>只在这票真的到港之后才显示</b>（预报到港不算，没到港就写「未到港」）。
          <b>乐航（14450）</b>值 = 到港日<b>下一周</b>的那一班，<b>只看到港是周几，跟渠道无关</b>：
          周一到 → 下周二；周二到 → 下周五；周三/四/五/六/日到 → 下周六。
          <b>恒信（LM739）</b>值 = 从到港日起按渠道算：
          美森 周一～周三到 → 当周周六、周四～周日到 → 下周六；以星 +13 天；盐田 +14 天。
          被<b>美国海关（国外）查验</b>的：正常上架日整体后移 14 天；
          已放行且放行日更晚的，再按周顺延到不早于放行日。
          出口查验 / 国内查验发生在开船前，船期里已经含了这段延误，<b>不再加两周</b>。
        </p>
        <p>
          <b>超期</b>：只对<b>真的到港了</b>的单判 —— 今天超过预测上架日、且还没到「已上架」。
          还没到港的单不参与超期判定（另有「超预计到货日未到港」单独提示）。
        </p>
        <p>
          <b>帕沃尔怎么算「上架」</b>：不看状态文字，看<b>箱数</b> ——
          已入箱数 = 总箱数 ⇒ 已上架；已入箱数 = 0 ⇒ 未上架；中间 ⇒ 部分入库
          （状态可能还写「海外仓待入库」，但箱数已经在动了）。
        </p>
        <p>
          <b>自动清理</b>：每次定时抓取都会<b>删掉已上架满 3 天</b>和<b>已取消</b>的单
          （用户 2026-11-05 定）。所以这张表只留「还要盯的货」，历史去帕沃尔后台或领星查。
          要临时看回更久，跑 <code>--keep-shelved-days 30</code>。
        </p>
        <p>
          <b>生成</b>：<code>python scripts/goods/fetch_shipment_track.py</code> ——
          拉两个账号的入库单 → 查恒信（只查 LM739）→ 读历史表标定 → 落成
          <code>shipment-track.json</code>。定时任务 <code>TEMU-Shipment-Track-Daily</code>。
        </p>
      </div>
    </el-card>
  </div>
</template>

<script setup lang="ts">
  import ShipmentTrackAPI from "@/api/temu/shipment-track";
  import type { ShipmentRow, ShipmentTrackData } from "@/api/temu/shipment-track";

  defineOptions({ name: "TemuShipmentTrack" });

  const loading = ref(false);
  const data = ref<ShipmentTrackData>({
    generatedAt: "",
    source: { primary: "", accounts: [], tracking: "", calibration: "" },
    leadTime: {
      overall: { n: 0, mean: null, median: 8, p80: null, min: null, max: null },
      channels: {},
      fallback: 8,
      note: "",
    },
    summary: {
      total: 0, waiting: 0, onShelf: 0, receiving: 0, received: 0, canceled: 0,
      arrived: 0, notArrived: 0, arrivedWaiting: 0, overdue: 0, abnormal: 0, noTrace: 0,
      issues: 0, totalQty: 0, waitingQty: 0, byStatus: {}, byChannel: {},
    },
    rows: [],
  });

  /**
   * 默认视角 = 全部。
   * （2026-11-04 用户要求：原来默认停在「超期未上架」，从库存跟踪的「在途」
   * 点过来时只看得到超期那几票，看着像"搜错了"。默认全量，想看超期再点一下。）
   */
  const viewPick = ref<"all" | "overdue" | "arrived" | "waiting" | "shelved">("all");
  const accountPick = ref("all");
  const chPick = ref("all");
  const stPick = ref("all");
  const keyword = ref("");
  const onlyIssue = ref(false);

  const page = ref(1);
  const pageSize = ref(50);

  const traceOpen = ref(false);
  const traceRow = ref<ShipmentRow | null>(null);

  async function load() {
    loading.value = true;
    try {
      data.value = await ShipmentTrackAPI.getShipmentTrack();
    } catch (e) {
      ElMessage.error(e instanceof Error ? e.message : "加载头程跟踪数据失败");
    } finally {
      loading.value = false;
    }
  }

  /**
   * 支持用 ?view=xxx 直达某个视图。
   * 大屏面板上「近 3 天到港 → 上架」点进来就是 ?view=shelved（已上架），
   * 不然用户落地在「超期未上架」，还得自己再点一下。
   */
  const route = useRoute();
  const VIEWS = ["all", "overdue", "arrived", "waiting", "shelved"] as const;

  /**
   * 从 **URL 本身** 解析 query（而不是只信 useRoute）。
   *
   * ⚠ 2026-11-04 真正的根因（第四轮才定位到）：
   *   ① 本项目的 keep-alive 缓存键是 `route.fullPath` + 外层 `:key="route.fullPath"`
   *      （layouts/components/LayoutMain.vue），所以「带 query」和不带 query 是**两个实例**；
   *   ② 在 hash 模式下，页面之间复用同一个标签时 `useRoute().query` 有时仍是旧对象，
   *      组件里读到的 `kw` 是空 —— 搜索框自然还是上一次的词。
   * 解决办法：直接读 `location.hash` 里的 query，谁也不用重新挂载或重启。
   * 这一段不依赖 route 对象，`onMounted` / `onActivated` / `hashchange` 都会调用它。
   */
  function rawQuery(): Record<string, string> {
    const h = String(window.location.hash || "");
    const i = h.indexOf("?");
    const out: Record<string, string> = {};
    if (i < 0) return out;
    for (const part of h.slice(i + 1).split("&")) {
      if (!part) continue;
      const [k, v = ""] = part.split("=");
      out[decodeURIComponent(k)] = decodeURIComponent(v.replace(/\+/g, " "));
    }
    return out;
  }

  function applyViewFromQuery() {
    const v = String(route.query.view || rawQuery().view || "");
    if ((VIEWS as readonly string[]).includes(v)) {
      viewPick.value = v as (typeof VIEWS)[number];
    }
  }

  /**
   * 本次文档是不是「强制刷新（F5 / Ctrl+R）」打开的。
   *
   * `main.ts` 在应用启动时把 `performance` 的 navigation type 记到
   * `window.__TEMU_NAV_TYPE__`（"reload" / "navigate" / "back_forward"），
   * 同时记下启动时的 hash。**不能在这里现读 performance** —— 页面组件是懒加载的，
   * 等它执行时用户可能已经在 reload 出来的文档里点过几次路由，
   * 现读永远得到 "reload"，会把「刷新后再从库存跟踪点进来」也误清。
   *
   * 再加一道：启动 hash 必须**就是这个页面且带 kw** 才算「刷新残留」。
   */
  function pageWasReloadedWithKw(): boolean {
    try {
      const w = window as unknown as Record<string, unknown>;
      if (w.__TEMU_NAV_TYPE__ !== "reload") return false;
      const boot = String(w.__TEMU_BOOT_HASH__ || "");
      return /shipment-track/i.test(boot) && /[?&]kw=/i.test(boot);
    } catch {
      return false;
    }
  }

  /** 已经在本 document 里抹掉过一次 kw ⇒ 之后以 location.hash 为准（route.query 是旧对象） */
  const KW_STRIPPED_KEY = "__temuTrackKwStripped";
  const kwStripped = () => Boolean((window as unknown as Record<string, unknown>)[KW_STRIPPED_KEY]);

  /** 把地址栏里的 `kw=` 摘掉，保留其余参数（view 等） */
  function stripKwFromHash() {
    const h = String(window.location.hash || "");
    const i = h.indexOf("?");
    if (i < 0) return;
    const path = h.slice(0, i);
    const keep = h
      .slice(i + 1)
      .split("&")
      .filter((p) => p && !/^kw=/i.test(p));
    const next = keep.length ? `${path}?${keep.join("&")}` : path;
    if (next !== h) {
      try {
        window.history.replaceState(window.history.state, "", next);
      } catch {
        /* 忽略：抹不掉也不影响下面的清空逻辑 */
      }
    }
    (window as unknown as Record<string, unknown>)[KW_STRIPPED_KEY] = true;
  }

  /**
   * `?kw=` 关键词落地：库存跟踪页的「在途」列点过来时带的款号/套系/尺码，
   * 落地直接筛到那几票货，不用再手动搜一遍。
   * （2026-11-04 用户要求：在途列不要悬浮提示，直接跳到头程跟踪看这一款。）
   *
   * ⚠ 2026-11-19 用户报的 bug：「从库存跟踪带关键词跳进来后，**强制刷新**（F5），
   *   关键词不会清除」—— 地址栏的 `?kw=…` 一直在，刷新当然还是那个筛选，
   *   而 keep-alive 又把这个实例连同 keyword 一起缓存了。
   *   用户口径：**刷新 = 重新开始**，回到不带关键词的全量视图。
   *   做法：刷新打开的文档里，第一次应用时把 kw 从地址栏抹掉并清空搜索框；
   *   之后（同一次文档内的 SPA 跳转 / 从库存跟踪再点进来）照常按链接筛。
   */
  function applyKwFromQuery() {
    if (pageWasReloadedWithKw() && !kwStripped()) {
      stripKwFromHash();
      if (keyword.value !== "") {
        keyword.value = "";
        page.value = 1;
      }
      return;
    }
    // 抹过 kw 之后 route.query 还停着旧值，只能信 location.hash
    const fromRoute = kwStripped() ? "" : String(route.query.kw || "").trim();
    const v = fromRoute || String(rawQuery().kw || "").trim();
    // 没有 kw 参数时也清空：从别处带着旧词再进来，不能残留上一次的搜索
    if (v !== keyword.value) {
      keyword.value = v;
      page.value = 1;
    }
  }

  onMounted(() => {
    applyViewFromQuery();
    applyKwFromQuery();
    load();
  });

  /**
   * ⚠ 2026-11-04 实测定案：这个项目的 keep-alive 缓存键是 **route.fullPath**，
   * 而且外面还套了一层 `:key="route.fullPath"` 的 wrapper（见 layouts/components/LayoutMain.vue）。
   *
   *   · 从菜单进（fullPath 无 query）→ 缓存 A，实例 1
   *   · 从「在途」带 ?view=all&kw=… 进来 → fullPath 变了 → 生成**另一个 wrapper/实例 2**
   *   · 返回菜单再点头程跟踪 → fullPath 回到无 query → 命中缓存 A → 显示的是**实例 1**
   *     （它还保留着上一次的 keyword），而 inst 1 的 onActivated 只会重放它自己的旧 query
   *
   * 这就是「搜索框一直显示上一次的词」。修法：激活时**以当前路由为准**重新套用，
   * 而不是依赖组件内部保存的状态；同时无 kw 时清空。
   */
  onActivated(() => {
    applyViewFromQuery();
    applyKwFromQuery();
  });

  /** hash 里 query 变了也要跟上（同标签页内 $router.push 不会重建实例） */
  const onHashChange = () => {
    applyViewFromQuery();
    applyKwFromQuery();
  };
  onMounted(() => window.addEventListener("hashchange", onHashChange));
  onUnmounted(() => window.removeEventListener("hashchange", onHashChange));

  watch(
    () => [route.query.view, route.query.kw],
    () => {
      applyViewFromQuery();
      applyKwFromQuery();
    }
  );

  // ---------------------------------------------------------------- 数字与标签
  const num = (v?: number | null) => (v == null ? "—" : Math.round(Number(v)).toLocaleString("zh-CN"));

  /** el-table 插槽给的是宽类型 DefaultRow，统一按 ShipmentRow 接 */
  const asRow = (row: unknown) => row as ShipmentRow;

  const logTag = (s: string) =>
    s === "delivered" ? "success" : s === "in_transit" || s === "picked" ? "warning" : s ? "info" : "danger";

  const omsTag = (s?: number) =>
    s === 4 ? "success" : s === 1 ? "warning" : s === 5 ? "danger" : s === 2 || s === 3 ? "primary" : "info";

  const issueTag = (i: string) =>
    i.includes("超期") ? "danger" : i.includes("取消") ? "danger" : i.includes("异常") ? "warning"
      : i.includes("未上架") ? "warning" : "info";

  /** 美国海关查验未放行时起算的天数（与 config/headmile_eta.json 的 inspection_release_days 一致） */
  const INSPECTION_DAYS = 14;

  /** 超期整行标红，扫表时不用逐格读 */
  const rowClass = ({ row }: { row: ShipmentRow }) =>
    row.overdueDays > 0 ? "is-alert" : "";

  // ---------------------------------------------------------------- 指标
  const stats = computed(() => {
    const s = data.value.summary;
    return [
      { label: "入库单总数", value: num(s.total), hint: `合计 ${num(s.totalQty)} 件`, color: "#0f766e" },
      {
        label: "超期未上架",
        value: num(s.overdue),
        hint: "过了预测上架日还没上架 ← 先看这个",
        color: s.overdue ? "#b91c1c" : "#16a34a",
      },
      {
        label: "已到港未上架",
        value: num(s.arrivedWaiting),
        hint: `已到港 ${s.arrived} ｜ 未到港 ${s.notArrived}`,
        color: "#d97706",
      },
      {
        label: "待入库",
        value: num(s.waiting),
        hint: `其中收货中 ${s.receiving}`,
        color: "#e6a23c",
      },
      { label: "已上架", value: num(s.onShelf), hint: "近 3 天内上架的（满 3 天已清出跟踪表）", color: "#16a34a" },
      {
        label: "物流异常",
        value: num(s.abnormal),
        hint: "最新节点是延迟 / 转船 / 查验",
        color: s.abnormal ? "#dc2626" : "#16a34a",
      },
      { label: "需关注", value: num(s.issues), hint: "超期 / 异常 / 未到港超期", color: "#7c3aed" },
    ];
  });

  const accountOpts = computed(() => {
    const m: Record<string, string> = {};
    for (const r of data.value.rows) {
      if (!r.account) continue;
      // 14450 已经不走领星了（2026-11-02 领星停用，改抓帕沃尔头程），
      // 标签直接点明来源，免得看的人以为 OMS 还在更新
      const tag = r.source === "帕沃尔头程" ? "帕沃尔头程" : r.whName || r.account;
      m[r.account] = tag;
    }
    return Object.keys(m).map((k) => ({ value: k, label: `${k} · ${m[k]}` }));
  });

  /**
   * 到港日期是不是只是「预计到港」（货代还没报实际到港）。
   * 参数按 `unknown` 接：el-table 插槽给的是 DefaultRow，旧写法这里报 TS2345。
   */
  function isForecast(row: unknown): boolean {
    return String((row as ShipmentRow).arriveSource || "").includes("forecast");
  }

  /**
   * 这票**真的到港了**没有（预报到港不算）。
   * 2026-11-18 用户要求「预测上架列到港后再显示」⇒ 判定就用这个：
   * 有到港日期 且 不是「预计到港」。
   */
  function hasArrived(row: unknown): boolean {
    const r = row as ShipmentRow;
    return Boolean(r.arriveDate) && !isForecast(r);
  }

  /** 帕沃尔头程那批（14450）：预测列写「大概送仓」，不是「预测上架 + 文字描述」 */
  function isPw(row: unknown): boolean {
    return String((row as ShipmentRow).source || "") === "帕沃尔头程";
  }

  /**
   * 这一票的**乐航运单号**（LH10059114）。查不到乐航轨迹的就返回空。
   *
   * 判断依据：logistics.shipmentId 形如 LH+数字。帕沃尔那批在乐航命中时
   * 后端会把 shipmentId 换成乐航的号；没命中时那里还是帕沃尔内部流水号
   * （2026…），所以要用正则挡掉，别把流水号当乐航号显示。
   */
  function lhNo(row: unknown): string {
    const id = String((row as ShipmentRow).logistics?.shipmentId || "");
    return /^LH\d+$/i.test(id) ? id : "";
  }

  /**
   * 货代查件页（免登录）模板。
   *
   * 为什么用外链：站内 `shipment-track.json` 一天只抓一次，
   * 「上次爬取」之外的最新节点只能去货代官网看。
   */
  /**
   * ⚠ 2026-11-19 修：必须是 `#/tracking?numbers=`，两处都不能错。
   *
   *  · 前端是 umi hash 路由 ⇒ 路由段要写在 `#` 后面，`/tracking/app?numbers=…`
   *    没有 hash 路由，umi 落不到任何页面，整屏渲染成「404页面」（用户截图）。
   *  · 页面读的单号参数名是 `numbers`（逗号分隔，最多 20 个），
   *    `tracking_number` 只有 `&inajax=1` 的 JSON 接口才认。
   */
  const LEHANG_TRACK = (no: string) =>
    `http://lehang.nextsls.com/tracking/app#/tracking?numbers=${encodeURIComponent(no)}`;
  const HENGXIN_TRACK = (no: string) =>
    `http://hengxe.nextsls.com/tracking/app#/tracking?numbers=${encodeURIComponent(no)}`;

  /** 「查最新轨迹」实际拿去查的那个号：乐航号 > 入库单号 */
  function traceNoOf(row: unknown): string {
    return lhNo(row) || (row as ShipmentRow).inboundNo;
  }

  /**
   * 「查最新轨迹」的目标地址：
   *   · 有乐航运单号 LH…  → 乐航查件页（带这个 LH 号）
   *   · 入库单号 IB…      → 恒信查件页（后端 trackUrl，同 hengxe.nextsls.com）
   *   · 帕沃尔 14450… 未命中乐航 → 回帕沃尔订单页（乐航查不了，别给死链）
   * 拿不到单号就返回空，页面上显示「—」，不给死链。
   *
   * ⚠ 2026-11-19 修：以前帕沃尔那批一律拼成乐航页，但乐航只认自己的 LH 号，
   *   拿帕沃尔 14450… 去查是「系统中暂无该单号」，白页。
   */
  function latestTraceUrl(row: unknown): string {
    const r = row as ShipmentRow;
    const lh = lhNo(r);
    if (lh) return LEHANG_TRACK(lh);
    if (/^IB/i.test(String(r.inboundNo || ""))) return r.trackUrl || HENGXIN_TRACK(r.inboundNo);
    if (r.source === "帕沃尔头程") return r.trackUrl || "";
    return r.trackUrl || "";
  }

  /** 已上架的单：到港 → 上架 实际用了几天（没有到港日就给 null） */
  function shelfDays(r: ShipmentRow): number | null {
    if (!r.shelfTime || !r.arriveDate) return null;
    const a = new Date(`${String(r.arriveDate).slice(0, 10)}T00:00:00`);
    const s = new Date(`${String(r.shelfTime).slice(0, 10)}T00:00:00`);
    if (Number.isNaN(a.getTime()) || Number.isNaN(s.getTime())) return null;
    return Math.round((s.getTime() - a.getTime()) / 86400000);
  }

  /**
   * 已上架视图的回看窗口。
   *
   * ⚠ 2026-11-05 用户要求「超过三天已上架的在定时更新时清理」⇒ 数据里
   *   最多只留 3 天的已上架；这里跟着改成 3，别再标「近 30 天」
   *   （否则页面写着 30 天、实际只有 3 天，看着像抓漏了）。
   *   要看更久的历史去帕沃尔后台或「已上架」之外的原始单据。
   */
  const SHELVED_DAYS = 3;

  /**
   * 上架日期是否落在近 N 天内（含今天）。
   * 按**上架日**算 —— 那才是「什么时候真的进仓能卖」。
   */
  function shelvedWithin(r: ShipmentRow, days: number): boolean {
    if (!r.shelfTime) return false;
    const d = new Date(`${String(r.shelfTime).slice(0, 10)}T00:00:00`);
    if (Number.isNaN(d.getTime())) return false;
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const back = Math.round((today.getTime() - d.getTime()) / 86400000);
    return back >= 0 && back < days;
  }

  // ---------------------------------------------------------------- 过滤
  const filtered = computed(() => {
    const kw = keyword.value.trim().toLowerCase();
    return data.value.rows
      .filter((r) => {
        if (viewPick.value === "overdue" && !(r.overdueDays > 0)) return false;
        if (viewPick.value === "arrived" && !(r.arriveDate && r.status !== 4 && r.status !== 5)) return false;
        if (viewPick.value === "waiting" && r.arriveDate) return false;
        // 已上架：只看真的上架了、且上架日在回看窗口内（现为 3 天）
        if (viewPick.value === "shelved" && !(r.status === 4 && shelvedWithin(r, SHELVED_DAYS))) return false;
        if (accountPick.value !== "all" && r.account !== accountPick.value) return false;
        if (chPick.value !== "all" && r.channelText !== chPick.value) return false;
        if (stPick.value !== "all" && r.statusText !== stPick.value) return false;
        if (onlyIssue.value && !r.issues.length) return false;
        if (!kw) return true;
        // 搜索也认乐航号（LH10059114），不然用户拿货代号来查会搜不到
        return [r.inboundNo, lhNo(r), r.whName, r.channelText, ...r.skus.map((s) => `${s.sku} ${s.productName} ${s.style} ${s.size}`)]
          .some((t) => String(t || "").toLowerCase().includes(kw));
      })
      .sort((a, b) => {
        // 已上架视图：按上架时间倒序（最近进仓的先看），跟催不催无关
        if (viewPick.value === "shelved") {
          return String(b.shelfTime || "").localeCompare(String(a.shelfTime || ""));
        }
        // 其余：超期天数降序（最该催的在最上）→ 到港已等天数降序 → 入库单号
        const o = b.overdueDays - a.overdueDays;
        if (o) return o;
        const w = (b.waitDays ?? -1) - (a.waitDays ?? -1);
        if (w) return w;
        return a.inboundNo.localeCompare(b.inboundNo);
      });
  });

  const filteredOverdue = computed(() => filtered.value.filter((r) => r.overdueDays > 0).length);
  const filteredArrivedWaiting = computed(
    () => filtered.value.filter((r) => r.arriveDate && r.status !== 4 && r.status !== 5).length
  );

  const viewTitle = computed(() => {
    if (viewPick.value === "overdue") return "超期未上架";
    if (viewPick.value === "arrived") return "已到港待上架";
    if (viewPick.value === "waiting") return "未到港";
    if (viewPick.value === "shelved") return `近 ${SHELVED_DAYS} 天已上架`;
    return "全部入库单";
  });

  const paged = computed(() => {
    const start = (page.value - 1) * pageSize.value;
    return filtered.value.slice(start, start + pageSize.value);
  });

  watch([viewPick, accountPick, chPick, stPick, keyword, onlyIssue], () => {
    page.value = 1;
  });

  function onSizeChange() {
    page.value = 1;
  }

  function openTraces(row: ShipmentRow) {
    traceRow.value = row;
    traceOpen.value = true;
  }

  // ---------------------------------------------------------------- 复制清单
  function copyTodo() {
    const rows = filtered.value;
    if (!rows.length) {
      ElMessage.info("当前筛选下没有货件");
      return;
    }
    const head = "入库单号\t乐航运单号\tOMS状态\t渠道\t到港日期\t已等天数\t预测上架日\t超期天数\t产品·尺码·数量\t需关注";
    const body = rows.map((r) =>
      [
        r.inboundNo, lhNo(r) || "", r.statusText, r.channelText, r.arriveDate || "",
        r.waitDays ?? "", r.predictedShelf || "", r.overdueDays || "",
        r.skus.map((s) => `${s.productName}×${s.qty}`).join("; "),
        r.issues.join(" / "),
      ].join("\t")
    );
    navigator.clipboard
      ?.writeText([head, ...body].join("\n"))
      .then(() => ElMessage.success(`已复制 ${rows.length} 张`))
      .catch(() => ElMessage.error("复制失败，请检查浏览器剪贴板权限"));
  }

  /**
   * 双击单号 → 复制那一个号（用户 2026-11-17 要求；2026-11-18 当天试过单击，
   * 又按用户「改回双击复制单号」恢复成双击）。
   *
   * 为什么用 dblclick：单号列左边是展开行，单击容易误触；
   * 双击只在明确要抄号时触发，且不冒泡（`.stop`）到行展开。
   * 复制失败退回 textarea + execCommand —— 线上是 http 站点，
   * `navigator.clipboard` 在非安全上下文里是 undefined，不兜底就只能看到报错。
   */
  function copyNo(text: string, label: string) {
    const t = String(text || "").trim();
    if (!t) return;
    const ok = () => ElMessage.success({ message: `已复制${label}：${t}`, duration: 1500 });
    if (navigator.clipboard?.writeText) {
      navigator.clipboard.writeText(t).then(ok).catch(() => legacyCopy(t, ok));
      return;
    }
    legacyCopy(t, ok);
  }

  function legacyCopy(t: string, ok: () => void) {
    try {
      const ta = document.createElement("textarea");
      ta.value = t;
      ta.style.position = "fixed";
      ta.style.opacity = "0";
      document.body.appendChild(ta);
      ta.select();
      document.execCommand("copy");
      document.body.removeChild(ta);
      ok();
    } catch {
      ElMessage.error("复制失败，请手动选中单号复制");
    }
  }
</script>

<style lang="scss" scoped>
  .sh-page {
    padding-bottom: 24px;
  }

  .sh-header {
    display: flex;
    flex-wrap: wrap;
    gap: 14px;
    align-items: flex-end;
    justify-content: space-between;
    margin-bottom: 12px;

    &__title {
      margin: 0 0 4px;
      font-size: 20px;
      font-weight: 700;
    }

    &__desc {
      max-width: 1040px;
      margin: 0;
      font-size: 12px;
      line-height: 1.7;
      color: var(--el-text-color-secondary);
    }

    &__meta {
      display: flex;
      flex-wrap: wrap;
      gap: 8px;
      align-items: center;
    }
  }

  .sh-alert {
    margin-bottom: 12px;

    &__body {
      font-size: 12px;
      line-height: 1.9;

      code {
        padding: 1px 5px;
        font-size: 11.5px;
        background: var(--el-fill-color-light);
        border-radius: 4px;
      }
    }

    &__ch {
      display: inline-block;
      margin-right: 12px;
    }
  }

  .sh-stats {
    margin-bottom: 12px;
  }

  .sh-stat {
    &__label {
      font-size: 12px;
      color: var(--el-text-color-secondary);
    }

    &__value {
      margin: 2px 0;
      font-size: 20px;
      font-weight: 600;
      font-variant-numeric: tabular-nums;
    }

    &__hint {
      font-size: 11px;
      color: var(--el-text-color-placeholder);
    }
  }

  .sh-bar {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    align-items: center;
    margin-bottom: 12px;
  }

  .sh-card {
    margin-bottom: 12px;

    &__head {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      align-items: baseline;
      justify-content: space-between;
    }

    &__title {
      font-size: 14px;
      font-weight: 600;
    }

    &__sub {
      margin-left: 6px;
      font-size: 11.5px;
      font-weight: 400;
      color: var(--el-text-color-placeholder);
    }
  }

  .sh-hint {
    font-size: 12px;
    color: var(--el-text-color-secondary);
  }

  .sh-table {
    :deep(.el-table__cell) {
      font-variant-numeric: tabular-nums;
    }

    /**
     * 超期行：整行淡红底。
     * ⚠ 不能写死 #fff5f5 —— 面板支持深色主题，死白底 + 浅色字 = 看不清
     *   （2026-11-02 用户反馈「超期未上架的 css 过于白了，看不清」）。
     *   用 Element 自己的语义色变量，深浅主题各自会换算。
     */
    :deep(.el-table__row.is-alert) {
      --el-table-tr-bg-color: var(--el-color-danger-light-9);
    }

    :deep(.el-table__row.is-alert > td.el-table__cell) {
      background: var(--el-color-danger-light-9);
    }

    /* 悬停时别把超期底色盖掉（el-table 默认 hover 会覆盖成 --el-table-row-hover-bg-color） */
    :deep(.el-table__row.is-alert:hover > td.el-table__cell) {
      background: var(--el-color-danger-light-8);
    }
  }

  .sh-issues {
    display: flex;
    flex-wrap: wrap;
    gap: 4px;
    min-width: 0;
  }

  .sh-mono {
    font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
    font-size: 12px;
  }

  /* 乐航运单号（LH10059114）：比帕沃尔参考单号显眼一档，
     因为用户拿它去乐航查件、跟货代对账都用这个号 */
  .sh-lh {
    font-family: ui-monospace, SFMono-Regular, Consolas, monospace;
    font-size: 12px;
    font-weight: 600;
    color: var(--el-color-primary);
    line-height: 1.3;
  }

  /* 可双击复制的单号：hover 给点反馈，让人知道这儿能点 */
  .sh-copyable {
    cursor: copy;
    border-radius: 2px;

    &:hover {
      background: var(--el-color-primary-light-9);
    }

    &:active {
      background: var(--el-color-primary-light-8);
    }
  }

  .sh-node {
    font-size: 12px;
    line-height: 1.4;

    &.is-bad {
      color: var(--el-color-danger);
      font-weight: 600;
    }

    /* 点这一格弹「上次爬取的整条轨迹」（2026-11-18 起，原「轨迹」按钮取消） */
    &--open {
      cursor: pointer;
      border-radius: 2px;

      &:hover {
        text-decoration: underline;
        text-underline-offset: 2px;
        background: var(--el-color-primary-light-9);
      }
    }

    &__time {
      font-size: 11px;
      color: var(--el-text-color-placeholder);
    }

    /* 轨迹来源小标签（乐航）：一眼看出这是货代全链路，不是帕沃尔那三条 */
    .sh-src {
      margin-left: 4px;
      padding: 0 3px;
      border-radius: 2px;
      font-size: 10px;
      color: var(--el-color-success);
      background: var(--el-color-success-light-9);
    }

    /* 恒信查不到时那条「去货代查件页 →」链接 */
    &__link {
      display: inline-block;
      margin-top: 2px;
      font-size: 11.5px;
    }
  }

  .sh-sku {
    display: inline-block;
    margin-right: 10px;
    font-size: 11.5px;
    color: var(--el-text-color-regular);
    white-space: nowrap;

    b {
      color: var(--el-text-color-primary);
    }
  }

  .sh-issue {
    max-width: 100%;
    margin: 0;
    overflow: hidden;
    /* 不许被 flex 压扁 —— 不加这句标签会被挤成「超期 +」这种半截
       （2026-11-02 用户截图反馈）。宽度不够就让它们换行。 */
    flex: 0 0 auto;

    /* el-tag 默认 nowrap，长文案会顶出单元格、盖住右边的固定「操作」列。
       这里允许省略号，全文放悬停提示里。 */
    :deep(.el-tag__content) {
      overflow: hidden;
      text-overflow: ellipsis;
      white-space: nowrap;
    }
  }

  .sh-dim {
    color: var(--el-text-color-placeholder);
  }

  .sh-tiny {
    font-size: 10.5px;
  }

  .sh-bad {
    color: var(--el-color-danger);
    font-weight: 600;
  }

  .sh-ok {
    color: var(--el-color-success);
    font-weight: 600;
  }

  .sh-warn {
    color: var(--el-color-warning);
    font-weight: 600;
  }

  .sh-exp {
    padding: 4px 8px 8px 40px;

    &__head {
      display: flex;
      flex-wrap: wrap;
      gap: 10px;
      align-items: center;
      margin-bottom: 8px;
      font-size: 12px;
    }

    &__t {
      max-width: 980px;
    }
  }

  .sh-pagebar {
    display: flex;
    justify-content: flex-end;
    margin-top: 10px;
  }

  .sh-drawer__meta {
    display: flex;
    flex-wrap: wrap;
    gap: 10px;
    align-items: center;
    margin-bottom: 8px;
    font-size: 12px;
  }

  .sh-drawer__note {
    margin: 0 0 14px;
    font-size: 11.5px;
    line-height: 1.7;
  }

  .sh-timeline {
    padding-left: 4px;
    font-size: 12.5px;
  }

  .sh-rule {
    &__body {
      font-size: 12px;
      line-height: 1.85;
      color: var(--el-text-color-regular);

      p {
        margin: 0 0 4px;
      }

      code {
        padding: 1px 5px;
        font-size: 11.5px;
        background: var(--el-fill-color-light);
        border-radius: 4px;
      }
    }
  }
</style>
