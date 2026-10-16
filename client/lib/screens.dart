import 'package:flutter/material.dart';
import 'package:url_launcher/url_launcher.dart';

import 'api.dart';
import 'main.dart' show ink, teal;
import 'models.dart';

const sectionNames = {'research': '研究进展', 'industry': '产业动态', 'world': '重大事件'};
const kindNames = {
  'journal': '期刊',
  'direction': '研究方向',
  'research': '研究方向',
  'company': '公司',
  'business': '公司业务',
  'application': '工业应用',
  'macro': '宏观与政策',
  'topic': '关注领域',
};
String errorText(Object error) =>
    error is ApiFailure ? error.message : '暂时无法完成操作，请稍后重试。';
void toast(BuildContext context, String message) =>
    ScaffoldMessenger.of(context).showSnackBar(
      SnackBar(content: Text(message), behavior: SnackBarBehavior.floating),
    );
Future<void> openLink(BuildContext context, String value) async {
  final uri = Uri.tryParse(value);
  if (uri == null ||
      !['https', 'http'].contains(uri.scheme) ||
      uri.host.isEmpty) {
    toast(context, '暂无可打开的来源链接。');
    return;
  }
  try {
    if (!await launchUrl(uri, mode: LaunchMode.externalApplication) &&
        context.mounted) {
      toast(context, '无法打开链接，请检查系统浏览器。');
    }
  } catch (_) {
    if (context.mounted) toast(context, '无法打开链接，请检查系统浏览器。');
  }
}

class HomeScreen extends StatefulWidget {
  final Api api;
  final String? initialError;
  const HomeScreen({super.key, required this.api, this.initialError});
  @override
  State<HomeScreen> createState() => _HomeScreenState();
}

class _HomeScreenState extends State<HomeScreen> with WidgetsBindingObserver {
  int _page = 0;
  int _request = 0;
  bool _loading = false, _offline = false, _companyBookmarks = false;
  String? _error;
  Json _bootstrap = {}, _briefing = {};
  List<NewsItem> _items = [];
  List<Company> _companies = [];
  String _section = '', _journal = '', _date = shanghaiToday();
  final _search = TextEditingController();
  @override
  void initState() {
    super.initState();
    WidgetsBinding.instance.addObserver(this);
    _error = widget.initialError;
    _initialize();
  }

  @override
  void dispose() {
    WidgetsBinding.instance.removeObserver(this);
    _search.dispose();
    super.dispose();
  }

  @override
  void didChangeAppLifecycleState(AppLifecycleState state) {
    if (state == AppLifecycleState.resumed && mounted && !_loading) {
      if (_page == 0) {
        setState(() => _date = shanghaiToday());
      }
      _load();
    }
  }

  Future<void> _initialize() async {
    if (!widget.api.connection.configured) return;
    final cached = await widget.api.connection.cachedBriefing();
    if (mounted && cached != null) {
      setState(() {
        _briefing = cached;
        _items = objects(cached['items']).map(NewsItem.new).toList();
        _offline = true;
      });
    }
    await _load();
  }

  Future<void> _load() async {
    if (!widget.api.connection.configured) {
      if (mounted) setState(() => _loading = false);
      return;
    }
    final request = ++_request;
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      // Bootstrap is fetched with each refresh so preferences reflect other devices.
      final bootstrap = await widget.api.bootstrap();
      Json data;
      final companies = _page == 2 || (_page == 3 && _companyBookmarks);
      if (companies) {
        data = await widget.api.call(
          '/api/companies',
          query: {
            'q': _search.text.trim(),
            'bookmarked': _page == 3 ? 'true' : 'false',
          },
        );
      } else {
        final historicalQuery = _page == 1 &&
            (_search.text.trim().isNotEmpty || _journal.isNotEmpty);
        data = await widget.api.briefing({
          'date': _page == 0
              ? shanghaiToday()
              : _page == 1 && !historicalQuery
                  ? _date
                  : '',
          'q': _page == 0 ? '' : _search.text.trim(),
          'section': _section,
          'journal': _page == 1 ? _journal : '',
          'bookmarked': _page == 3 ? 'true' : 'false',
        });
      }
      if (!mounted || request != _request) return;
      setState(() {
        _bootstrap = bootstrap;
        _offline = false;
        if (companies) {
          _companies = objects(data['items']).map(Company.new).toList();
        } else {
          _briefing = data;
          _items = objects(data['items']).map(NewsItem.new).toList();
        }
      });
      if (_page == 0 && _section.isEmpty) {
        await widget.api.connection.cacheBriefing(data);
      }
    } catch (error) {
      if (!mounted || request != _request) return;
      setState(() {
        _error = errorText(error);
        _offline = _items.isNotEmpty || _companies.isNotEmpty;
      });
    } finally {
      if (mounted && request == _request) setState(() => _loading = false);
    }
  }

  void _navigate(int page) {
    if (_page == page) return;
    setState(() {
      _page = page;
      _section = '';
      _journal = '';
      _search.clear();
      _items = [];
      _companies = [];
      _offline = false;
      _error = null;
    });
    _load();
  }

  Future<void> _settings() async {
    final saved = await showDialog<bool>(
      context: context,
      builder: (_) => ConnectionDialog(api: widget.api),
    );
    if (saved == true && mounted) {
      setState(() {
        _items = [];
        _companies = [];
        _bootstrap = {};
        _briefing = {};
        _offline = false;
      });
      await _load();
    }
  }

  Future<void> _interests() async {
    if (_bootstrap.isEmpty) {
      toast(context, '请先连接服务器并刷新。');
      return;
    }
    final saved = await showModalBottomSheet<bool>(
      context: context,
      isScrollControlled: true,
      useSafeArea: true,
      showDragHandle: true,
      constraints: const BoxConstraints(maxWidth: 800),
      builder: (_) => InterestsSheet(api: widget.api, bootstrap: _bootstrap),
    );
    if (saved == true) await _load();
  }

  Future<void> _openItem(NewsItem item) async {
    await Navigator.of(context).push(
      MaterialPageRoute(
        builder: (_) => ArticleScreen(api: widget.api, initial: item),
      ),
    );
    if (mounted) _load();
  }

  Future<void> _openCompany(Company company) async {
    await Navigator.of(context).push(
      MaterialPageRoute(
        builder: (_) => CompanyScreen(api: widget.api, initial: company),
      ),
    );
    if (mounted) _load();
  }

  Future<void> _bookmarkItem(NewsItem item) async {
    final mark = !item.bookmarked;
    try {
      await widget.api.bookmark(item.id, mark);
      if (mounted) setState(() => item.raw['bookmarked'] = mark);
    } catch (error) {
      if (mounted) toast(context, errorText(error));
    }
  }

  Future<void> _pickDate() async {
    final selected = await showDatePicker(
      context: context,
      initialDate: DateTime.tryParse(_date) ?? DateTime.now(),
      firstDate: DateTime(2020),
      lastDate: DateTime.parse(shanghaiToday()),
    );
    if (selected != null) {
      setState(() {
        _date = selected.toIso8601String().substring(0, 10);
        _search.clear();
        _journal = '';
      });
      _load();
    }
  }

  @override
  Widget build(BuildContext context) {
    final wide = MediaQuery.sizeOf(context).width >= 900;
    return Scaffold(
      body: SafeArea(
        child: Row(
          children: [
            if (wide) _sidebar(),
            Expanded(
              child: Column(
                children: [
                  Padding(
                    padding: EdgeInsets.fromLTRB(
                      wide ? 36 : 20,
                      16,
                      wide ? 36 : 12,
                      8,
                    ),
                    child: Row(
                      children: [
                        if (!wide) ...[
                          const BrandMark(size: 32),
                          const SizedBox(width: 10),
                          const Text(
                            'Optinfo',
                            style: TextStyle(
                              fontWeight: FontWeight.w700,
                              fontSize: 20,
                              letterSpacing: -.8,
                            ),
                          ),
                        ],
                        if (wide)
                          Text(
                            'YOUR DAILY INTELLIGENCE',
                            style: TextStyle(
                              fontSize: 11,
                              fontWeight: FontWeight.w600,
                              color: ink.withValues(alpha: .5),
                              letterSpacing: 1.7,
                            ),
                          ),
                        const Spacer(),
                        IconButton(
                          onPressed: _interests,
                          icon: const Icon(Icons.tune_rounded),
                          tooltip: '关注领域',
                        ),
                        IconButton(
                          onPressed: _loading ? null : _load,
                          icon: const Icon(Icons.refresh_rounded),
                          tooltip: '刷新内容',
                        ),
                        IconButton(
                          onPressed: _settings,
                          icon: const Icon(Icons.settings_outlined),
                          tooltip: '连接设置',
                        ),
                      ],
                    ),
                  ),
                  if (_loading)
                    const LinearProgressIndicator(minHeight: 2)
                  else
                    const SizedBox(height: 2),
                  Expanded(
                    child: RefreshIndicator(
                      onRefresh: _load,
                      child: ListView(
                        padding: EdgeInsets.fromLTRB(
                          wide ? 36 : 20,
                          24,
                          wide ? 36 : 20,
                          40,
                        ),
                        children: [
                          Center(
                            child: ConstrainedBox(
                              constraints: const BoxConstraints(maxWidth: 1160),
                              child: Column(
                                crossAxisAlignment: CrossAxisAlignment.start,
                                children: [
                                  _heading(),
                                  const SizedBox(height: 24),
                                  if (!widget.api.connection.configured)
                                    _welcome()
                                  else ...[
                                    if (_error != null || _offline) ...[
                                      StatusNotice(
                                        message: _offline
                                            ? '显示最近加载的内容。${_error ?? '正在获取最新资讯…'}'
                                            : _error!,
                                        icon: _offline
                                            ? Icons.cloud_off_outlined
                                            : Icons.info_outline,
                                        action: TextButton(
                                          onPressed: _settings,
                                          child: const Text('连接设置'),
                                        ),
                                      ),
                                      const SizedBox(height: 20),
                                    ],
                                    _filters(),
                                    const SizedBox(height: 20),
                                    if (_page == 2 ||
                                        (_page == 3 && _companyBookmarks))
                                      _companyGrid()
                                    else
                                      _feed(),
                                  ],
                                ],
                              ),
                            ),
                          ),
                        ],
                      ),
                    ),
                  ),
                ],
              ),
            ),
          ],
        ),
      ),
      bottomNavigationBar: wide
          ? null
          : NavigationBar(
              selectedIndex: _page,
              onDestinationSelected: _navigate,
              destinations: const [
                NavigationDestination(
                  icon: Icon(Icons.wb_sunny_outlined),
                  selectedIcon: Icon(Icons.wb_sunny_rounded),
                  label: '今日',
                ),
                NavigationDestination(
                  icon: Icon(Icons.history_rounded),
                  label: '历史',
                ),
                NavigationDestination(
                  icon: Icon(Icons.business_outlined),
                  selectedIcon: Icon(Icons.business_rounded),
                  label: '企业',
                ),
                NavigationDestination(
                  icon: Icon(Icons.bookmark_border_rounded),
                  selectedIcon: Icon(Icons.bookmark_rounded),
                  label: '收藏',
                ),
              ],
            ),
    );
  }

  Widget _sidebar() => Container(
        width: 224,
        decoration: const BoxDecoration(
          color: Colors.white,
          border: Border(right: BorderSide(color: Color(0xFFE0E6E0))),
        ),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            const Padding(
              padding: EdgeInsets.fromLTRB(24, 32, 24, 40),
              child: Row(
                children: [
                  BrandMark(),
                  SizedBox(width: 12),
                  Expanded(
                    child: FittedBox(
                      fit: BoxFit.scaleDown,
                      alignment: Alignment.centerLeft,
                      child: Text(
                        'Optinfo',
                        style: TextStyle(
                          fontSize: 25,
                          fontWeight: FontWeight.w700,
                          letterSpacing: -1,
                        ),
                      ),
                    ),
                  ),
                ],
              ),
            ),
            for (final (index, icon, label) in [
              (0, Icons.wb_sunny_outlined, '今日简报'),
              (1, Icons.history_rounded, '历史资讯'),
              (2, Icons.business_outlined, '企业图鉴'),
              (3, Icons.bookmark_border_rounded, '我的收藏'),
            ])
              Padding(
                padding:
                    const EdgeInsets.symmetric(horizontal: 14, vertical: 4),
                child: Material(
                  color: _page == index
                      ? const Color(0xFFE4F0E9)
                      : Colors.transparent,
                  borderRadius: BorderRadius.circular(12),
                  child: ListTile(
                    shape: RoundedRectangleBorder(
                      borderRadius: BorderRadius.circular(12),
                    ),
                    leading: Icon(
                      icon,
                      color: _page == index ? teal : ink.withValues(alpha: .6),
                    ),
                    title: Text(
                      label,
                      style: TextStyle(
                        fontWeight:
                            _page == index ? FontWeight.w700 : FontWeight.w400,
                      ),
                    ),
                    onTap: () => _navigate(index),
                  ),
                ),
              ),
            const Spacer(),
            Padding(
              padding: const EdgeInsets.all(24),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  const Text(
                    '保持好奇，有据可循。',
                    style: TextStyle(fontSize: 12, fontWeight: FontWeight.w600),
                  ),
                  const SizedBox(height: 8),
                  Text(
                    '光学 · 半导体 · 更大的世界\n每日 04:00 更新 · 北京时间',
                    style: TextStyle(
                      fontSize: 11,
                      height: 1.8,
                      color: ink.withValues(alpha: .5),
                    ),
                  ),
                ],
              ),
            ),
          ],
        ),
      );
  Widget _heading() {
    final title = ['今日，值得知道的事', '让线索连接起来', '认识创新背后的企业', '留下重要的发现'][_page];
    final subtitle = [
      '精选研究、产业进展与重大事件',
      '按日期回看，或跨日期检索期刊与关键词',
      '业务方向、职业信息与持续更新的产业动态',
      '收藏的资讯与企业，随时回来继续阅读',
    ][_page];
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        Text(
          _page == 0
              ? '${shanghaiToday().replaceAll('-', ' / ')}   ·   DAILY BRIEF'
              : ['', 'ARCHIVE', 'COMPANY ATLAS', 'YOUR COLLECTION'][_page],
          style: const TextStyle(
            fontSize: 11,
            letterSpacing: 1.3,
            fontWeight: FontWeight.w700,
            color: teal,
          ),
        ),
        const SizedBox(height: 12),
        Text(title, style: Theme.of(context).textTheme.headlineMedium),
        const SizedBox(height: 8),
        Text(subtitle, style: TextStyle(color: ink.withValues(alpha: .6))),
        if (_page == 0 && _offline && string(_briefing['date']).isNotEmpty)
          Padding(
            padding: const EdgeInsets.only(top: 8),
            child: Text(
              '缓存简报日期：${_briefing['date']}',
              style: const TextStyle(color: teal, fontSize: 12),
            ),
          ),
      ],
    );
  }

  Widget _welcome() => Surface(
        child: Padding(
          padding: const EdgeInsets.all(28),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: [
              const Icon(Icons.hub_outlined, size: 36, color: teal),
              const SizedBox(height: 20),
              Text('连接你的私人资讯库', style: Theme.of(context).textTheme.titleLarge),
              const SizedBox(height: 10),
              const Text(
                '先在这台设备上连接 Tailscale，再填写服务器 HTTPS 地址和设备访问令牌。内容由服务器每日凌晨四点整理，打开应用即可阅读。',
              ),
              const SizedBox(height: 24),
              FilledButton.icon(
                onPressed: _settings,
                icon: const Icon(Icons.arrow_forward_rounded),
                label: const Text('设置连接'),
              ),
            ],
          ),
        ),
      );
  Widget _filters() => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (_page != 0) ...[
            TextField(
              controller: _search,
              onSubmitted: (_) => _load(),
              textInputAction: TextInputAction.search,
              decoration: InputDecoration(
                hintText: _page == 2 || (_page == 3 && _companyBookmarks)
                    ? '搜索企业名称或业务'
                    : '搜索关键词，发现历史资讯',
                prefixIcon: const Icon(Icons.search_rounded),
                suffixIcon: IconButton(
                  onPressed: _load,
                  icon: const Icon(Icons.arrow_forward_rounded),
                  tooltip: '搜索',
                ),
              ),
            ),
            const SizedBox(height: 16),
          ],
          if (_page == 3) ...[
            SegmentedButton<bool>(
              segments: const [
                ButtonSegment(
                  value: false,
                  label: Text('资讯'),
                  icon: Icon(Icons.article_outlined),
                ),
                ButtonSegment(
                  value: true,
                  label: Text('企业'),
                  icon: Icon(Icons.business_outlined),
                ),
              ],
              selected: {_companyBookmarks},
              onSelectionChanged: (values) {
                setState(() {
                  _companyBookmarks = values.first;
                  _items = [];
                  _companies = [];
                });
                _load();
              },
            ),
            const SizedBox(height: 16),
          ],
          if (_page != 2 && !(_page == 3 && _companyBookmarks))
            Wrap(
              spacing: 8,
              runSpacing: 10,
              crossAxisAlignment: WrapCrossAlignment.center,
              children: [
                for (final entry in {'': '全部', ...sectionNames}.entries)
                  ChoiceChip(
                    label: Text(entry.value),
                    selected: _section == entry.key,
                    onSelected: (_) {
                      setState(() => _section = entry.key);
                      _load();
                    },
                    showCheckmark: false,
                  ),
                if (_page == 0)
                  ActionChip(
                    avatar: const Icon(Icons.tune_rounded, size: 16),
                    label: const Text('调整关注'),
                    onPressed: _interests,
                  ),
                if (_page == 1)
                  ActionChip(
                    avatar: const Icon(Icons.calendar_today_outlined, size: 16),
                    label: Text(_date),
                    onPressed: _pickDate,
                  ),
                if (_page == 1)
                  SizedBox(
                    width: 200,
                    child: DropdownButtonFormField<String>(
                      initialValue: objects(_bootstrap['journals'])
                              .any((j) => string(j['id']) == _journal)
                          ? _journal
                          : '',
                      isExpanded: true,
                      decoration: const InputDecoration(
                        labelText: '按期刊查看',
                        isDense: true,
                      ),
                      items: [
                        const DropdownMenuItem(value: '', child: Text('全部期刊')),
                        ...objects(_bootstrap['journals']).map(
                          (j) => DropdownMenuItem(
                            value: string(j['id']),
                            child: Text(
                              string(j['name']),
                              overflow: TextOverflow.ellipsis,
                            ),
                          ),
                        ),
                      ],
                      onChanged: (value) {
                        setState(() => _journal = value ?? '');
                        _load();
                      },
                    ),
                  ),
              ],
            ),
          if (_page == 1 &&
              (_search.text.trim().isNotEmpty || _journal.isNotEmpty))
            const Padding(
              padding: EdgeInsets.only(top: 12),
              child: Text(
                '当前搜索覆盖全部日期。点击日期可返回单日简报。',
                style: TextStyle(fontSize: 12, color: teal),
              ),
            ),
        ],
      );
  Widget _feed() {
    final visible =
        _items.where((i) => _section.isEmpty || i.section == _section).toList();
    final status = string(_briefing['status']);
    final editionMissing = status == 'missing' || status == 'not_generated';
    final editionExists = status == 'ready' || status == 'partial';
    if (visible.isEmpty) {
      final todayTitle = editionMissing
          ? '今天的简报还未就绪'
          : editionExists
              ? _section == 'world'
                  ? '暂无达到筛选标准的重大事件'
                  : _section.isNotEmpty
                      ? '该分区暂无新内容'
                      : '今天暂无新内容'
              : '暂时无法读取今日简报';
      final todayMessage = editionMissing
          ? '服务器每天 04:00（北京时间）生成内容。生成后点击刷新即可阅读。'
          : editionExists
              ? '目前没有符合条件的新资讯。可以查看其他分区，或稍后刷新。'
              : '请检查连接状态后刷新重试。';
      return EmptyState(
        icon: _page == 3
            ? Icons.bookmark_border_rounded
            : Icons.auto_stories_outlined,
        title: _loading
            ? '正在整理阅读列表…'
            : _page == 3
                ? '还没有收藏的资讯'
                : _page == 0
                    ? todayTitle
                    : '没有找到相关资讯',
        message: _loading
            ? '稍候片刻，好内容正在路上。'
            : _page == 0
                ? todayMessage
                : '试试其他日期、关键词或筛选条件。',
      );
    }
    return Column(
      crossAxisAlignment: CrossAxisAlignment.start,
      children: [
        if (_page == 0 && status == 'partial') ...[
          const StatusNotice(message: '已生成部分内容，后续会自动补齐。'),
          const SizedBox(height: 16),
        ],
        Row(
          children: [
            Text(
              '${visible.length} 条资讯',
              style: const TextStyle(fontSize: 12, fontWeight: FontWeight.w600),
            ),
            const Spacer(),
            if (_page == 0)
              const Flexible(
                child: Text(
                  '打开即读 · 来源可追溯',
                  textAlign: TextAlign.end,
                  style: TextStyle(fontSize: 11, color: Colors.black45),
                ),
              ),
          ],
        ),
        const SizedBox(height: 14),
        if (_page == 0 && _section.isEmpty) ...[
          for (final entry in sectionNames.entries)
            if (visible.any((item) => item.section == entry.key)) ...[
              Padding(
                padding: const EdgeInsets.only(top: 20, bottom: 14),
                child: Row(children: [
                  Text(entry.value,
                      style: Theme.of(context).textTheme.titleMedium),
                  const SizedBox(width: 14),
                  const Expanded(child: Divider()),
                ]),
              ),
              for (final item
                  in visible.where((item) => item.section == entry.key))
                _itemCard(item),
            ],
        ] else
          for (final item in visible) _itemCard(item),
      ],
    );
  }

  Widget _itemCard(NewsItem item) => Padding(
        padding: const EdgeInsets.only(bottom: 14),
        child: NewsCard(
            item: item,
            onTap: () => _openItem(item),
            onBookmark: () => _bookmarkItem(item)),
      );

  Widget _companyGrid() {
    if (_companies.isEmpty) {
      return EmptyState(
        icon: Icons.business_outlined,
        title: _loading
            ? '正在读取企业图鉴…'
            : _page == 3
                ? '还没有收藏的企业'
                : '暂无匹配企业',
        message: '可从资讯提到的企业进入详情并收藏。',
      );
    }
    return LayoutBuilder(
      builder: (context, constraints) {
        final columns = constraints.maxWidth >= 850
            ? 3
            : constraints.maxWidth >= 550
                ? 2
                : 1;
        return Wrap(
          spacing: 16,
          runSpacing: 16,
          children: _companies
              .map(
                (company) => SizedBox(
                  width: (constraints.maxWidth - 16 * (columns - 1)) / columns,
                  child: Surface(
                    child: InkWell(
                      borderRadius: BorderRadius.circular(18),
                      onTap: () => _openCompany(company),
                      child: Padding(
                        padding: const EdgeInsets.all(22),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            Row(
                              children: [
                                CompanyLogo(company: company),
                                const Spacer(),
                                if (company.bookmarked)
                                  const Icon(
                                    Icons.bookmark_rounded,
                                    color: teal,
                                    size: 20,
                                  ),
                                const SizedBox(width: 8),
                                const Icon(
                                  Icons.north_east_rounded,
                                  size: 18,
                                  color: Colors.black38,
                                ),
                              ],
                            ),
                            const SizedBox(height: 18),
                            Text(
                              company.name,
                              style: Theme.of(context).textTheme.titleMedium,
                            ),
                            if (company.nameEn.isNotEmpty)
                              Text(
                                company.nameEn,
                                maxLines: 1,
                                overflow: TextOverflow.ellipsis,
                                style: const TextStyle(
                                  color: Colors.black45,
                                  fontSize: 11,
                                ),
                              ),
                            const SizedBox(height: 10),
                            Text(
                              company.description.isEmpty
                                  ? '企业资料持续补充中。'
                                  : company.description,
                              maxLines: 3,
                              overflow: TextOverflow.ellipsis,
                            ),
                            const SizedBox(height: 16),
                            Text(
                              company.listing,
                              style: const TextStyle(fontSize: 11, color: teal),
                            ),
                          ],
                        ),
                      ),
                    ),
                  ),
                ),
              )
              .toList(),
        );
      },
    );
  }
}

class BrandMark extends StatelessWidget {
  final double size;
  const BrandMark({super.key, this.size = 38});
  @override
  Widget build(BuildContext context) => Container(
        width: size,
        height: size,
        decoration: BoxDecoration(
          color: teal,
          borderRadius: BorderRadius.circular(size * .3),
        ),
        child: Icon(
          Icons.blur_circular_rounded,
          size: size * .72,
          color: Colors.white,
        ),
      );
}

class Surface extends StatelessWidget {
  final Widget child;
  const Surface({super.key, required this.child});
  @override
  Widget build(BuildContext context) => Material(
        color: Colors.white,
        shape: RoundedRectangleBorder(
          borderRadius: BorderRadius.circular(18),
          side: const BorderSide(color: Color(0xFFE1E7E1)),
        ),
        clipBehavior: Clip.antiAlias,
        child: child,
      );
}

class EmptyState extends StatelessWidget {
  final IconData icon;
  final String title, message;
  const EmptyState({
    super.key,
    required this.icon,
    required this.title,
    required this.message,
  });
  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.symmetric(vertical: 50, horizontal: 20),
        child: Center(
          child: Column(
            children: [
              Container(
                padding: const EdgeInsets.all(20),
                decoration: const BoxDecoration(
                  color: Color(0xFFE7EFE7),
                  shape: BoxShape.circle,
                ),
                child: Icon(icon, size: 32, color: teal),
              ),
              const SizedBox(height: 22),
              Text(
                title,
                textAlign: TextAlign.center,
                style: Theme.of(context).textTheme.titleMedium,
              ),
              const SizedBox(height: 8),
              ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 420),
                child: Text(
                  message,
                  textAlign: TextAlign.center,
                  style: const TextStyle(color: Colors.black54),
                ),
              ),
            ],
          ),
        ),
      );
}

class StatusNotice extends StatelessWidget {
  final String message;
  final IconData icon;
  final Widget? action;
  const StatusNotice({
    super.key,
    required this.message,
    this.icon = Icons.info_outline_rounded,
    this.action,
  });
  @override
  Widget build(BuildContext context) => Container(
        padding: const EdgeInsets.all(16),
        decoration: BoxDecoration(
          color: const Color(0xFFF0F1E8),
          borderRadius: BorderRadius.circular(14),
        ),
        child: Row(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Icon(icon, size: 20, color: teal),
            const SizedBox(width: 10),
            Expanded(
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(message, style: const TextStyle(fontSize: 12)),
                  if (action != null) action!,
                ],
              ),
            ),
          ],
        ),
      );
}

class NewsCard extends StatelessWidget {
  final NewsItem item;
  final VoidCallback onTap, onBookmark;
  const NewsCard({
    super.key,
    required this.item,
    required this.onTap,
    required this.onBookmark,
  });
  @override
  Widget build(BuildContext context) => Surface(
        child: InkWell(
          onTap: onTap,
          child: Padding(
            padding: const EdgeInsets.fromLTRB(22, 20, 14, 22),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Row(
                  children: [
                    Container(
                      width: 6,
                      height: 6,
                      decoration: BoxDecoration(
                        color: item.kind == 'major'
                            ? const Color(0xFFB16B42)
                            : teal,
                        shape: BoxShape.circle,
                      ),
                    ),
                    const SizedBox(width: 7),
                    Text(
                      sectionNames[item.section] ?? '资讯',
                      style: const TextStyle(
                        fontSize: 11,
                        fontWeight: FontWeight.w600,
                        color: teal,
                      ),
                    ),
                    const SizedBox(width: 12),
                    Expanded(
                      child: Text(
                        item.journal.isNotEmpty
                            ? item.journal
                            : item.sourceName,
                        maxLines: 1,
                        overflow: TextOverflow.ellipsis,
                        style: const TextStyle(
                            fontSize: 11, color: Colors.black45),
                      ),
                    ),
                    IconButton(
                      onPressed: onBookmark,
                      tooltip: item.bookmarked ? '取消收藏' : '收藏资讯',
                      icon: Icon(
                        item.bookmarked
                            ? Icons.bookmark_rounded
                            : Icons.bookmark_border_rounded,
                        color: item.bookmarked ? teal : Colors.black38,
                        size: 21,
                      ),
                    ),
                  ],
                ),
                Text(
                  item.title,
                  style: Theme.of(context)
                      .textTheme
                      .titleLarge
                      ?.copyWith(fontSize: 19),
                ),
                if (item.summary.isNotEmpty) ...[
                  const SizedBox(height: 10),
                  Text(
                    item.summary,
                    maxLines: 3,
                    overflow: TextOverflow.ellipsis,
                    style: const TextStyle(color: Color(0xFF5B6966)),
                  ),
                ],
                const SizedBox(height: 16),
                Wrap(
                  spacing: 10,
                  runSpacing: 6,
                  children: [
                    Text(
                      item.date,
                      style:
                          const TextStyle(fontSize: 11, color: Colors.black45),
                    ),
                    ...item.tags.take(3).map(
                          (tag) => Text(
                            '# $tag',
                            style: const TextStyle(fontSize: 11, color: teal),
                          ),
                        ),
                  ],
                ),
              ],
            ),
          ),
        ),
      );
}

class ConnectionDialog extends StatefulWidget {
  final Api api;
  const ConnectionDialog({super.key, required this.api});
  @override
  State<ConnectionDialog> createState() => _ConnectionDialogState();
}

class _ConnectionDialogState extends State<ConnectionDialog> {
  late final _address = TextEditingController(text: widget.api.connection.base);
  late final _token = TextEditingController(text: widget.api.connection.token);
  String? _error;
  bool _saving = false, _showToken = false;
  @override
  void dispose() {
    _address.dispose();
    _token.dispose();
    super.dispose();
  }

  Future<void> _save() async {
    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      final base = ConnectionStore.normalize(_address.text);
      final test = ConnectionStore()
        ..base = base
        ..token = _token.text.trim();
      if (test.token.isEmpty) throw ApiFailure('请填写设备访问令牌。');
      await Api(test).bootstrap();
      await widget.api.connection.save(base, test.token);
      if (mounted) Navigator.pop(context, true);
    } catch (error) {
      if (mounted) setState(() => _error = errorText(error));
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  @override
  Widget build(BuildContext context) => AlertDialog(
        title: const Text('连接你的服务器'),
        content: SizedBox(
          width: 480,
          child: SingleChildScrollView(
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Text(
                  '使用现有 Tailscale 私人网络。请先确认本设备的 Tailscale 已连接。',
                  style: TextStyle(fontSize: 13, color: Colors.black54),
                ),
                const SizedBox(height: 24),
                TextField(
                  controller: _address,
                  autocorrect: false,
                  enableSuggestions: false,
                  keyboardType: TextInputType.url,
                  decoration: const InputDecoration(
                    labelText: '服务器 HTTPS 地址',
                    hintText: 'https://your-server.ts.net:8443',
                    helperText: '填写 Optinfo 服务地址，不含 /api 后缀',
                  ),
                ),
                const SizedBox(height: 16),
                TextField(
                  controller: _token,
                  obscureText: !_showToken,
                  autocorrect: false,
                  enableSuggestions: false,
                  decoration: InputDecoration(
                    labelText: '设备访问令牌',
                    suffixIcon: IconButton(
                      tooltip: _showToken ? '隐藏令牌' : '显示令牌',
                      onPressed: () => setState(() => _showToken = !_showToken),
                      icon: Icon(
                        _showToken
                            ? Icons.visibility_off_outlined
                            : Icons.visibility_outlined,
                      ),
                    ),
                  ),
                ),
                const SizedBox(height: 14),
                const Text(
                  '令牌保存在设备的系统安全存储中。个人服务器地址和令牌不会随安装包分发。',
                  style: TextStyle(fontSize: 11, color: Colors.black54),
                ),
                TextButton(
                  onPressed: () => showLicensePage(
                      context: context,
                      applicationName: 'Optinfo',
                      applicationVersion: '0.1.0'),
                  child: const Text('开源许可', style: TextStyle(fontSize: 11)),
                ),
                if (_error != null)
                  Padding(
                    padding: const EdgeInsets.only(top: 16),
                    child: StatusNotice(message: _error!),
                  ),
              ],
            ),
          ),
        ),
        actions: [
          TextButton(
            onPressed: _saving ? null : () => Navigator.pop(context),
            child: const Text('取消'),
          ),
          FilledButton(
            onPressed: _saving ? null : _save,
            child: Text(_saving ? '正在验证连接…' : '验证并保存'),
          ),
        ],
      );
}

class InterestsSheet extends StatefulWidget {
  final Api api;
  final Json bootstrap;
  const InterestsSheet({super.key, required this.api, required this.bootstrap});
  @override
  State<InterestsSheet> createState() => _InterestsSheetState();
}

class ArticleScreen extends StatefulWidget {
  final Api api;
  final NewsItem initial;
  const ArticleScreen({super.key, required this.api, required this.initial});
  @override
  State<ArticleScreen> createState() => _ArticleScreenState();
}

class _ArticleScreenState extends State<ArticleScreen> {
  late NewsItem _item = widget.initial;
  bool _saving = false;
  String? _error;
  @override
  void initState() {
    super.initState();
    _reload();
  }

  Future<void> _reload() async {
    try {
      final item = await widget.api.item(_item.id);
      if (mounted) {
        setState(() {
          _item = item;
          _error = null;
        });
      }
    } catch (error) {
      if (mounted) setState(() => _error = errorText(error));
    }
  }

  Future<void> _bookmark() async {
    setState(() => _saving = true);
    try {
      final marked = !_item.bookmarked;
      await widget.api.bookmark(_item.id, marked);
      if (mounted) setState(() => _item.raw['bookmarked'] = marked);
    } catch (error) {
      if (mounted) toast(context, errorText(error));
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final wide = MediaQuery.sizeOf(context).width >= 1100;
    return Scaffold(
      appBar: AppBar(
        title: Text(
          sectionNames[_item.section] ?? '资讯详情',
          style: const TextStyle(fontSize: 15),
        ),
        actions: [
          IconButton(
            onPressed: _saving ? null : _bookmark,
            tooltip: _item.bookmarked ? '取消收藏' : '收藏资讯',
            icon: Icon(
              _item.bookmarked
                  ? Icons.bookmark_rounded
                  : Icons.bookmark_border_rounded,
              color: teal,
            ),
          ),
          if (_item.sourceUrl.isNotEmpty)
            IconButton(
              onPressed: () => openLink(context, _item.sourceUrl),
              icon: const Icon(Icons.open_in_new_rounded),
              tooltip: '阅读原文',
            ),
          const SizedBox(width: 12),
        ],
      ),
      body: SelectionArea(
        child: SingleChildScrollView(
          child: Center(
            child: ConstrainedBox(
              constraints: const BoxConstraints(maxWidth: 1250),
              child: Padding(
                padding: EdgeInsets.fromLTRB(
                  wide ? 36 : 20,
                  24,
                  wide ? 36 : 20,
                  60,
                ),
                child: wide
                    ? Row(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          Expanded(flex: 7, child: _article()),
                          const SizedBox(width: 36),
                          Expanded(flex: 4, child: _aside()),
                        ],
                      )
                    : Column(
                        crossAxisAlignment: CrossAxisAlignment.start,
                        children: [
                          _article(),
                          const SizedBox(height: 32),
                          _aside(),
                        ],
                      ),
              ),
            ),
          ),
        ),
      ),
    );
  }

  Widget _article() => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          if (_error != null) ...[
            StatusNotice(message: '详情刷新失败，正在显示已加载的内容。$_error'),
            const SizedBox(height: 20),
          ],
          Wrap(
            spacing: 10,
            runSpacing: 6,
            children: [
              Text(
                _item.journal.isNotEmpty ? _item.journal : _item.sourceName,
                style: const TextStyle(
                  color: teal,
                  fontSize: 12,
                  fontWeight: FontWeight.w600,
                ),
              ),
              Text(
                _item.date,
                style: const TextStyle(fontSize: 12, color: Colors.black45),
              ),
            ],
          ),
          const SizedBox(height: 18),
          Text(_item.title, style: Theme.of(context).textTheme.headlineMedium),
          const SizedBox(height: 24),
          if (_item.summary.isNotEmpty)
            Container(
              width: double.infinity,
              padding: const EdgeInsets.all(22),
              decoration: const BoxDecoration(
                color: Color(0xFFEAF1E9),
                border: Border(left: BorderSide(color: teal, width: 3)),
              ),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    _item.kind == 'paper' ? '成果概览' : '发生了什么',
                    style: const TextStyle(
                      fontSize: 11,
                      fontWeight: FontWeight.w700,
                      color: teal,
                    ),
                  ),
                  const SizedBox(height: 10),
                  Text(
                    _item.summary,
                    style: const TextStyle(fontSize: 16, height: 1.9),
                  ),
                ],
              ),
            ),
          if (_item.evidence.isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(top: 14),
              child: Text(
                '阅读依据 · ${evidenceLabel(_item.evidence)}',
                style: const TextStyle(fontSize: 11, color: Colors.black54),
              ),
            ),
          _contentSection(
            _item.kind == 'paper' ? '01' : '',
            _item.kind == 'paper' ? '研究背景' : '事件背景',
            string(_item.content['background']),
          ),
          _contentSection(
            _item.kind == 'paper' ? '02' : '',
            _item.kind == 'paper' ? '研究方案' : '关键进展',
            string(_item.content['approach']),
          ),
          _contentSection(
            _item.kind == 'paper' ? '03' : '',
            _item.kind == 'paper' ? '研究结果' : '已知结果与影响',
            string(_item.content['results']),
          ),
          for (final figure in _item.figures) FigureView(figure: figure),
          if (_item.kind == 'paper' && _item.figures.isEmpty)
            const Padding(
              padding: EdgeInsets.only(top: 12),
              child: Text(
                '本条尚未取得可展示的原文图片，可通过来源链接查看。',
                style: TextStyle(fontSize: 12, color: Colors.black45),
              ),
            ),
          _contentSection(
            _item.kind == 'paper' ? '04' : '',
            _item.kind == 'paper' ? '总结 · 优势与不足' : '观察与不确定性',
            string(_item.content['assessment']),
          ),
          const SizedBox(height: 32),
          const Divider(),
          const SizedBox(height: 16),
          Text('来源与证据', style: Theme.of(context).textTheme.titleMedium),
          const SizedBox(height: 12),
          if (_item.sources.isEmpty && _item.sourceUrl.isNotEmpty)
            SourceLink(
              title: _item.sourceName.isEmpty ? '阅读原文' : _item.sourceName,
              url: _item.sourceUrl,
            ),
          for (final source in _item.sources)
            SourceLink(
                title: string(source['title']), url: string(source['url'])),
          if (string(_item.raw['doi']).isNotEmpty)
            Padding(
              padding: const EdgeInsets.only(top: 8),
              child: Text(
                'DOI: ${_item.raw['doi']}',
                style: const TextStyle(fontSize: 12, color: Colors.black54),
              ),
            ),
        ],
      );
  Widget _contentSection(String number, String title, String content) {
    if (content.trim().isEmpty) return const SizedBox.shrink();
    return Padding(
      padding: const EdgeInsets.only(top: 32),
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Row(
            children: [
              if (number.isNotEmpty) ...[
                Text(
                  number,
                  style: const TextStyle(
                    color: teal,
                    fontSize: 13,
                    fontWeight: FontWeight.w700,
                  ),
                ),
                const SizedBox(width: 12),
              ],
              Expanded(
                child: Text(
                  title,
                  style: Theme.of(context).textTheme.titleLarge,
                ),
              ),
            ],
          ),
          const SizedBox(height: 14),
          Text(content, style: Theme.of(context).textTheme.bodyLarge),
        ],
      ),
    );
  }

  Widget _aside() => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          ChatPanel(api: widget.api, itemId: _item.id),
          if (_item.companyIds.isNotEmpty) ...[
            const SizedBox(height: 28),
            Text('本文提到的企业', style: Theme.of(context).textTheme.titleMedium),
            const SizedBox(height: 12),
            for (final id in _item.companyIds)
              MentionedCompany(api: widget.api, companyId: id),
          ],
          if (_item.tags.isNotEmpty) ...[
            const SizedBox(height: 24),
            Wrap(
              spacing: 8,
              runSpacing: 8,
              children: _item.tags
                  .map(
                    (tag) => Chip(
                      label: Text(tag, style: const TextStyle(fontSize: 11)),
                      backgroundColor: Colors.white,
                      side: const BorderSide(color: Color(0xFFE1E7E1)),
                    ),
                  )
                  .toList(),
            ),
          ],
        ],
      );
}

String evidenceLabel(String value) =>
    {
      'abstract': '基于题录与摘要',
      'abstract_only': '基于题录与摘要',
      'fulltext': '基于可访问全文',
      'full_text': '基于可访问全文',
      'official': '官方来源',
      'primary': '原始来源',
      'metadata': '基于元数据',
      'news': '公开新闻来源',
      'feed': '订阅源摘要',
      'source_article': '原始报道全文',
      'source_excerpt': '原始报道节选',
      'institutional': '机构授权全文',
    }[value] ??
    value;

class FigureView extends StatelessWidget {
  final Json figure;
  const FigureView({super.key, required this.figure});
  @override
  Widget build(BuildContext context) {
    final url = string(figure['url']);
    final uri = Uri.tryParse(url);
    return Padding(
      padding: const EdgeInsets.only(top: 22),
      child: Surface(
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            if (uri != null && uri.scheme == 'https' && uri.host.isNotEmpty)
              Image.network(
                url,
                width: double.infinity,
                fit: BoxFit.contain,
                loadingBuilder: (context, child, progress) => progress == null
                    ? child
                    : const SizedBox(
                        height: 180,
                        child: Center(
                          child: CircularProgressIndicator(strokeWidth: 2),
                        ),
                      ),
                errorBuilder: (_, error, trace) => const SizedBox(
                  height: 150,
                  child: Center(
                    child: Text(
                      '原文图片暂时无法载入',
                      style: TextStyle(color: Colors.black45),
                    ),
                  ),
                ),
              )
            else
              const SizedBox(
                height: 100,
                child: Center(child: Text('暂无可展示的图片')),
              ),
            Padding(
              padding: const EdgeInsets.all(16),
              child: Column(
                crossAxisAlignment: CrossAxisAlignment.start,
                children: [
                  Text(
                    string(figure['caption']),
                    style: const TextStyle(fontSize: 12),
                  ),
                  if (string(figure['source_url']).isNotEmpty)
                    SourceLink(
                      title: '原文图片来源',
                      url: string(figure['source_url']),
                    ),
                ],
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class SourceLink extends StatelessWidget {
  final String title, url;
  const SourceLink({super.key, required this.title, required this.url});
  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.only(bottom: 6),
        child: InkWell(
          onTap: () => openLink(context, url),
          borderRadius: BorderRadius.circular(8),
          child: Padding(
            padding: const EdgeInsets.symmetric(vertical: 7),
            child: Row(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                const Icon(Icons.north_east_rounded, size: 16, color: teal),
                const SizedBox(width: 8),
                Expanded(
                  child: Text(
                    title.isNotEmpty ? title : Uri.tryParse(url)?.host ?? '来源',
                    style: const TextStyle(
                      fontSize: 12,
                      color: teal,
                      decoration: TextDecoration.underline,
                      decorationColor: Color(0xFFB7D2C9),
                    ),
                  ),
                ),
              ],
            ),
          ),
        ),
      );
}

class ChatPanel extends StatefulWidget {
  final Api api;
  final String itemId;
  const ChatPanel({super.key, required this.api, required this.itemId});
  @override
  State<ChatPanel> createState() => _ChatPanelState();
}

class _ChatPanelState extends State<ChatPanel> {
  final _question = TextEditingController();
  Json _chat = {};
  bool _loading = true, _sending = false;
  String? _error;
  String get _path => '/api/items/${Uri.encodeComponent(widget.itemId)}/chat';
  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    _question.dispose();
    super.dispose();
  }

  Future<void> _load() async {
    setState(() {
      _loading = true;
      _error = null;
    });
    try {
      final data = await widget.api.call(_path);
      if (mounted) setState(() => _chat = data);
    } catch (error) {
      if (mounted) setState(() => _error = errorText(error));
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _send() async {
    final message = _question.text.trim();
    if (message.isEmpty || _sending) return;
    setState(() {
      _sending = true;
      _error = null;
    });
    try {
      final data = await widget.api.call(
        _path,
        method: 'POST',
        body: {'message': message},
      );
      if (mounted) {
        setState(() {
          _chat = data;
          if (_question.text.trim() == message) _question.clear();
        });
      }
    } catch (error) {
      if (mounted) setState(() => _error = errorText(error));
    } finally {
      if (mounted) setState(() => _sending = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final messages = objects(_chat['messages']);
    final expiration = DateTime.tryParse(string(_chat['expires_at']));
    final expiresText = expiration == null
        ? '首次提问后创建会话，7 天后自动删除。'
        : '本会话于 ${expiration.toUtc().add(const Duration(hours: 8)).toIso8601String().substring(0, 16).replaceFirst('T', ' ')}（北京时间）自动删除。';
    return Surface(
      child: Padding(
        padding: const EdgeInsets.all(20),
        child: Column(
          crossAxisAlignment: CrossAxisAlignment.start,
          children: [
            Row(
              children: [
                Container(
                  padding: const EdgeInsets.all(8),
                  decoration: BoxDecoration(
                    color: const Color(0xFFEAF1E9),
                    borderRadius: BorderRadius.circular(10),
                  ),
                  child: const Icon(
                    Icons.auto_awesome_outlined,
                    size: 20,
                    color: teal,
                  ),
                ),
                const SizedBox(width: 10),
                Expanded(
                  child: Text(
                    '继续探究',
                    style: Theme.of(context).textTheme.titleMedium,
                  ),
                ),
                IconButton(
                  onPressed: _loading || _sending ? null : _load,
                  tooltip: '刷新会话',
                  icon: const Icon(Icons.refresh_rounded, size: 18),
                ),
              ],
            ),
            const SizedBox(height: 10),
            const Text(
              '向 DeepSeek Harness 提问。本条资讯的所有追问保存在同一个会话中。',
              style: TextStyle(fontSize: 12, color: Colors.black54),
            ),
            const SizedBox(height: 12),
            Text(
              expiresText,
              style: const TextStyle(fontSize: 11, color: teal),
            ),
            if (string(_chat['status']) == 'expired')
              const Padding(
                padding: EdgeInsets.only(top: 8),
                child: Text(
                  '上一次会话已到期删除，再次提问将开始新会话。',
                  style: TextStyle(fontSize: 12),
                ),
              ),
            const SizedBox(height: 18),
            if (_loading)
              const Padding(
                padding: EdgeInsets.all(16),
                child: Center(child: CircularProgressIndicator(strokeWidth: 2)),
              ),
            for (final message in messages)
              Padding(
                padding: const EdgeInsets.only(bottom: 14),
                child: Container(
                  width: double.infinity,
                  padding: const EdgeInsets.all(14),
                  decoration: BoxDecoration(
                    color: message['role'] == 'user'
                        ? const Color(0xFFEBF1EC)
                        : const Color(0xFFF6F7F3),
                    borderRadius: BorderRadius.circular(12),
                  ),
                  child: Column(
                    crossAxisAlignment: CrossAxisAlignment.start,
                    children: [
                      Text(
                        message['role'] == 'user' ? '你' : 'DeepSeek',
                        style: const TextStyle(
                          fontSize: 10,
                          color: teal,
                          fontWeight: FontWeight.w600,
                        ),
                      ),
                      const SizedBox(height: 6),
                      Text(
                        string(message['content']),
                        style: const TextStyle(fontSize: 13, height: 1.8),
                      ),
                    ],
                  ),
                ),
              ),
            if (_sending)
              const Padding(
                padding: EdgeInsets.only(bottom: 14),
                child: Row(
                  children: [
                    SizedBox(
                      width: 14,
                      height: 14,
                      child: CircularProgressIndicator(strokeWidth: 2),
                    ),
                    SizedBox(width: 10),
                    Expanded(
                      child: Text(
                        '正在结合来源分析，请稍候…',
                        style: TextStyle(fontSize: 12, color: teal),
                      ),
                    ),
                  ],
                ),
              ),
            if (_error != null) ...[
              StatusNotice(message: '$_error\n未发送成功的问题已保留，可再次重试。'),
              const SizedBox(height: 14),
            ],
            TextField(
              controller: _question,
              minLines: 2,
              maxLines: 6,
              maxLength: 4000,
              decoration: const InputDecoration(
                hintText: '例如：这个方案与已有方法相比，关键优势是什么？',
                hintStyle: TextStyle(fontSize: 12),
                counterStyle: TextStyle(fontSize: 10),
              ),
            ),
            const SizedBox(height: 12),
            Align(
              alignment: Alignment.centerRight,
              child: FilledButton.icon(
                onPressed: _sending ? null : _send,
                icon: const Icon(Icons.arrow_upward_rounded, size: 17),
                label: Text(_sending ? '思考中' : '发送问题'),
              ),
            ),
          ],
        ),
      ),
    );
  }
}

class CompanyLogo extends StatelessWidget {
  final Company company;
  final double size;
  const CompanyLogo({super.key, required this.company, this.size = 48});
  @override
  Widget build(BuildContext context) {
    final fallback = Center(
      child: Text(
        company.name.isEmpty ? '企' : company.name.characters.first,
        style: TextStyle(
          fontSize: size * .42,
          color: teal,
          fontWeight: FontWeight.w600,
        ),
      ),
    );
    return Container(
      width: size,
      height: size,
      padding: const EdgeInsets.all(7),
      decoration: BoxDecoration(
        color: const Color(0xFFEAF1E9),
        borderRadius: BorderRadius.circular(size * .25),
      ),
      child: company.logo.startsWith('https://')
          ? Image.network(
              company.logo,
              fit: BoxFit.contain,
              errorBuilder: (_, error, trace) => fallback,
            )
          : fallback,
    );
  }
}

class MentionedCompany extends StatefulWidget {
  final Api api;
  final String companyId;
  const MentionedCompany({
    super.key,
    required this.api,
    required this.companyId,
  });
  @override
  State<MentionedCompany> createState() => _MentionedCompanyState();
}

class _MentionedCompanyState extends State<MentionedCompany> {
  Company? _company;
  bool _saving = false;
  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final data = await widget.api.call(
        '/api/companies/${Uri.encodeComponent(widget.companyId)}',
      );
      if (mounted) setState(() => _company = Company(object(data['company'])));
    } catch (_) {
      /* Keep the identifier visible if company details are unavailable. */
    }
  }

  Future<void> _bookmark() async {
    final company = _company;
    if (company == null) return;
    setState(() => _saving = true);
    try {
      final mark = !company.bookmarked;
      await widget.api.bookmark(company.id, mark, company: true);
      if (mounted) setState(() => company.raw['bookmarked'] = mark);
    } catch (error) {
      if (mounted) toast(context, errorText(error));
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  @override
  Widget build(BuildContext context) => Padding(
        padding: const EdgeInsets.only(bottom: 8),
        child: Surface(
          child: ListTile(
            contentPadding:
                const EdgeInsets.symmetric(horizontal: 12, vertical: 6),
            leading: _company == null
                ? const Icon(Icons.business_outlined)
                : CompanyLogo(company: _company!, size: 36),
            title: Text(
              _company?.name ?? widget.companyId,
              style: const TextStyle(fontSize: 13),
            ),
            trailing: IconButton(
              onPressed: _saving || _company == null ? null : _bookmark,
              tooltip: _company?.bookmarked == true ? '取消收藏企业' : '收藏企业',
              icon: Icon(
                _company?.bookmarked == true
                    ? Icons.bookmark_rounded
                    : Icons.bookmark_border_rounded,
                size: 20,
                color: teal,
              ),
            ),
            onTap: _company == null
                ? _load
                : () async {
                    await Navigator.of(context).push(
                      MaterialPageRoute(
                        builder: (_) =>
                            CompanyScreen(api: widget.api, initial: _company!),
                      ),
                    );
                    _load();
                  },
          ),
        ),
      );
}

class CompanyScreen extends StatefulWidget {
  final Api api;
  final Company initial;
  const CompanyScreen({super.key, required this.api, required this.initial});
  @override
  State<CompanyScreen> createState() => _CompanyScreenState();
}

class _CompanyScreenState extends State<CompanyScreen> {
  late Company _company = widget.initial;
  List<NewsItem> _items = [];
  bool _loading = true, _saving = false;
  String? _error;
  @override
  void initState() {
    super.initState();
    _load();
  }

  Future<void> _load() async {
    try {
      final data = await widget.api.call(
        '/api/companies/${Uri.encodeComponent(_company.id)}',
      );
      if (mounted) {
        setState(() {
          _company = Company(object(data['company']));
          _items = objects(data['items']).map(NewsItem.new).toList();
          _error = null;
        });
      }
    } catch (error) {
      if (mounted) setState(() => _error = errorText(error));
    } finally {
      if (mounted) setState(() => _loading = false);
    }
  }

  Future<void> _bookmark() async {
    setState(() => _saving = true);
    try {
      final mark = !_company.bookmarked;
      await widget.api.bookmark(_company.id, mark, company: true);
      if (mounted) setState(() => _company.raw['bookmarked'] = mark);
    } catch (error) {
      if (mounted) toast(context, errorText(error));
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final pay = _company.compensation;
    return Scaffold(
      appBar: AppBar(
        title: const Text('企业图鉴', style: TextStyle(fontSize: 15)),
        actions: [
          IconButton(
            onPressed: _saving ? null : _bookmark,
            tooltip: _company.bookmarked ? '取消收藏企业' : '收藏企业',
            icon: Icon(
              _company.bookmarked
                  ? Icons.bookmark_rounded
                  : Icons.bookmark_border_rounded,
              color: teal,
            ),
          ),
          const SizedBox(width: 12),
        ],
      ),
      body: RefreshIndicator(
        onRefresh: _load,
        child: ListView(
          padding: const EdgeInsets.fromLTRB(20, 24, 20, 50),
          children: [
            Center(
              child: ConstrainedBox(
                constraints: const BoxConstraints(maxWidth: 900),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    if (_error != null) ...[
                      StatusNotice(message: _error!),
                      const SizedBox(height: 20),
                    ],
                    Row(
                      crossAxisAlignment: CrossAxisAlignment.start,
                      children: [
                        CompanyLogo(company: _company, size: 72),
                        const SizedBox(width: 20),
                        Expanded(
                          child: Column(
                            crossAxisAlignment: CrossAxisAlignment.start,
                            children: [
                              Text(
                                _company.name,
                                style:
                                    Theme.of(context).textTheme.headlineMedium,
                              ),
                              if (_company.nameEn.isNotEmpty)
                                Text(
                                  _company.nameEn,
                                  style: const TextStyle(color: Colors.black54),
                                ),
                              const SizedBox(height: 6),
                              Text(
                                _company.listing,
                                style: const TextStyle(
                                  color: teal,
                                  fontSize: 12,
                                ),
                              ),
                            ],
                          ),
                        ),
                      ],
                    ),
                    const SizedBox(height: 28),
                    Text(
                      _company.description.isEmpty
                          ? '企业介绍正在补充。'
                          : _company.description,
                      style: Theme.of(context).textTheme.bodyLarge,
                    ),
                    if (_company.website.isNotEmpty)
                      SourceLink(title: '企业官方网站', url: _company.website),
                    const SizedBox(height: 24),
                    Text('具体业务', style: Theme.of(context).textTheme.titleLarge),
                    const SizedBox(height: 12),
                    if (_company.businesses.isEmpty)
                      const Text(
                        '暂无已核实的业务资料。',
                        style: TextStyle(color: Colors.black54),
                      )
                    else
                      Wrap(
                        spacing: 8,
                        runSpacing: 8,
                        children: _company.businesses
                            .map(
                              (business) => Chip(
                                label: Text(business),
                                backgroundColor: Colors.white,
                              ),
                            )
                            .toList(),
                      ),
                    const SizedBox(height: 30),
                    Text(
                      '职业信息 · 年薪参考',
                      style: Theme.of(context).textTheme.titleLarge,
                    ),
                    const SizedBox(height: 12),
                    Surface(
                      child: Padding(
                        padding: const EdgeInsets.all(22),
                        child: Column(
                          crossAxisAlignment: CrossAxisAlignment.start,
                          children: [
                            _salary('硕士', pay['masters']),
                            const SizedBox(height: 16),
                            const Divider(),
                            const SizedBox(height: 16),
                            _salary('博士', pay['phd']),
                            if (string(pay['currency']).isNotEmpty ||
                                string(pay['as_of']).isNotEmpty)
                              Padding(
                                padding: const EdgeInsets.only(top: 16),
                                child: Text(
                                  [
                                    string(pay['currency']),
                                    string(pay['as_of']),
                                  ].where((s) => s.isNotEmpty).join(' · '),
                                  style: const TextStyle(
                                    fontSize: 11,
                                    color: Colors.black54,
                                  ),
                                ),
                              ),
                            if (string(pay['basis']).isNotEmpty)
                              Padding(
                                padding: const EdgeInsets.only(top: 10),
                                child: Text(
                                  string(pay['basis']),
                                  style: const TextStyle(
                                    fontSize: 12,
                                    color: Colors.black54,
                                  ),
                                ),
                              ),
                            const SizedBox(height: 12),
                            const Text(
                              '请结合岗位、地区、年份及基本工资 / 总包口径阅读；无可靠公开依据时不提供估算。',
                              style: TextStyle(
                                fontSize: 11,
                                color: Colors.black45,
                              ),
                            ),
                            for (final source in objects(pay['sources']))
                              SourceLink(
                                title: string(source['title']),
                                url: string(source['url']),
                              ),
                          ],
                        ),
                      ),
                    ),
                    if (objects(_company.raw['sources']).isNotEmpty) ...[
                      const SizedBox(height: 24),
                      Text(
                        '资料来源',
                        style: Theme.of(context).textTheme.titleMedium,
                      ),
                      for (final source in objects(_company.raw['sources']))
                        SourceLink(
                          title: string(source['title']),
                          url: string(source['url']),
                        ),
                    ],
                    const SizedBox(height: 36),
                    const Divider(),
                    const SizedBox(height: 24),
                    Text(
                      '相关历史资讯',
                      style: Theme.of(context).textTheme.titleLarge,
                    ),
                    const SizedBox(height: 16),
                    if (_loading)
                      const Center(
                        child: CircularProgressIndicator(strokeWidth: 2),
                      ),
                    if (!_loading && _items.isEmpty)
                      const EmptyState(
                        icon: Icons.article_outlined,
                        title: '新的动态还在路上',
                        message: '后续简报中提及这家企业的资讯会汇集在这里。',
                      ),
                    for (final item in _items)
                      Padding(
                        padding: const EdgeInsets.only(bottom: 14),
                        child: NewsCard(
                          item: item,
                          onTap: () async {
                            await Navigator.of(context).push(
                              MaterialPageRoute(
                                builder: (_) => ArticleScreen(
                                  api: widget.api,
                                  initial: item,
                                ),
                              ),
                            );
                            _load();
                          },
                          onBookmark: () async {
                            try {
                              final mark = !item.bookmarked;
                              await widget.api.bookmark(item.id, mark);
                              if (mounted) {
                                setState(() => item.raw['bookmarked'] = mark);
                              }
                            } catch (error) {
                              if (context.mounted) {
                                toast(context, errorText(error));
                              }
                            }
                          },
                        ),
                      ),
                  ],
                ),
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _salary(String degree, dynamic value) => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Text(
            degree,
            style: const TextStyle(
              color: teal,
              fontSize: 11,
              fontWeight: FontWeight.w600,
            ),
          ),
          const SizedBox(height: 6),
          Text(
            string(value).isEmpty ? '暂无可靠公开数据' : string(value),
            style: const TextStyle(fontSize: 15, height: 1.7),
          ),
        ],
      );
}

class _InterestsSheetState extends State<InterestsSheet> {
  late final Set<String> _selected = strings(
    widget.bootstrap[widget.bootstrap['effective_date'] != null
        ? 'pending_topics'
        : 'active_topics'],
  ).toSet();
  bool _saving = false;
  String? _error;
  Future<void> _save() async {
    setState(() {
      _saving = true;
      _error = null;
    });
    try {
      await widget.api.call(
        '/api/preferences',
        method: 'PUT',
        body: {'topic_ids': _selected.toList()},
      );
      if (mounted) {
        Navigator.pop(context, true);
        toast(context, '关注已保存，将从明天的简报开始生效。');
      }
    } catch (error) {
      if (mounted) setState(() => _error = errorText(error));
    } finally {
      if (mounted) setState(() => _saving = false);
    }
  }

  @override
  Widget build(BuildContext context) {
    final topics = objects(widget.bootstrap['topics']);
    final sections = objects(widget.bootstrap['sections']);
    return SizedBox(
      height: MediaQuery.sizeOf(context).height * .86,
      child: Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          Padding(
            padding: const EdgeInsets.fromLTRB(24, 4, 24, 16),
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  '让资讯更贴近你的研究',
                  style: Theme.of(context).textTheme.titleLarge,
                ),
                const SizedBox(height: 8),
                const Text(
                  '勾选领域会提高相关内容权重；未勾选的重大事件仍可能出现。修改从次日生效，今天的简报保持原样。',
                  style: TextStyle(fontSize: 12, color: Colors.black54),
                ),
              ],
            ),
          ),
          Expanded(
            child: ListView(
              padding: const EdgeInsets.symmetric(horizontal: 24),
              children: [
                for (final section in sections) ...[
                  const SizedBox(height: 12),
                  Text(
                    string(section['label']),
                    style: Theme.of(context).textTheme.titleMedium,
                  ),
                  if (string(section['description']).isNotEmpty)
                    Text(
                      string(section['description']),
                      style: const TextStyle(
                        fontSize: 12,
                        color: Colors.black54,
                      ),
                    ),
                  for (final kind in topics
                      .where((t) => t['section'] == section['id'])
                      .map((t) => string(t['kind']))
                      .toSet()) ...[
                    Padding(
                      padding: const EdgeInsets.only(top: 16, bottom: 8),
                      child: Text(
                        kindNames[kind] ?? kind,
                        style: const TextStyle(
                          fontSize: 11,
                          fontWeight: FontWeight.w600,
                          color: teal,
                        ),
                      ),
                    ),
                    Wrap(
                      spacing: 8,
                      runSpacing: 6,
                      children: topics
                          .where(
                            (t) =>
                                t['section'] == section['id'] &&
                                string(t['kind']) == kind,
                          )
                          .map(
                            (topic) => FilterChip(
                              label: Text(string(topic['label'])),
                              selected: _selected.contains(string(topic['id'])),
                              onSelected: (selected) => setState(() {
                                if (selected) {
                                  _selected.add(string(topic['id']));
                                } else {
                                  _selected.remove(string(topic['id']));
                                }
                              }),
                            ),
                          )
                          .toList(),
                    ),
                  ],
                  const SizedBox(height: 16),
                  const Divider(),
                ],
                if (topics.isEmpty)
                  const EmptyState(
                    icon: Icons.tune_rounded,
                    title: '关注目录尚未准备好',
                    message: '服务器初始化主题后，刷新即可选择。',
                  ),
              ],
            ),
          ),
          if (_error != null)
            Padding(
              padding: const EdgeInsets.symmetric(horizontal: 24),
              child: StatusNotice(message: _error!),
            ),
          Padding(
            padding: const EdgeInsets.all(24),
            child: Row(
              children: [
                Expanded(
                  child: Text(
                    '已选 ${_selected.length} 个领域',
                    style: const TextStyle(fontSize: 12, color: Colors.black54),
                  ),
                ),
                FilledButton(
                  onPressed: _saving ? null : _save,
                  child: Text(_saving ? '保存中…' : '保存 · 明天生效'),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
