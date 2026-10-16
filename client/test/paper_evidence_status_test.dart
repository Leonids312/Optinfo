import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:optinfo/api.dart';
import 'package:optinfo/models.dart';
import 'package:optinfo/screens.dart';

class EvidenceApi extends Api {
  final NewsItem article;
  EvidenceApi(this.article) : super(ConnectionStore());
  @override
  Future<NewsItem> item(String id) async => article;
  @override
  Future<Json> call(String path,
          {String method = 'GET',
          Json? body,
          Map<String, String>? query}) async =>
      {'messages': <Json>[], 'status': 'empty'};
}

void main() {
  testWidgets('only complete revision two papers hide the evidence warning',
      (tester) async {
    final cases = <Json>[
      {},
      {'analysis_status': 'pending_analysis', 'content_revision': 2},
      {'analysis_status': 'pending_fulltext', 'content_revision': 2},
      {'analysis_status': 'pending_figures', 'content_revision': 2},
      {'analysis_status': 'complete', 'content_revision': 1},
      {'analysis_status': 'complete', 'content_revision': 2},
    ];
    for (var index = 0; index < cases.length; index++) {
      final article = NewsItem({
        'id': 'evidence-$index',
        'title': '论文状态测试',
        'kind': 'paper',
        'section': 'research',
        'content': {'background': '已保存的正文仍可阅读。'},
        ...cases[index],
      });
      final api = EvidenceApi(article);
      await tester.pumpWidget(MaterialApp(
        home: ArticleScreen(
          key: ValueKey(index),
          api: api,
          initial: article,
        ),
      ));
      await tester.pumpAndSettle();
      expect(find.textContaining('当前为待校验解读'),
          index == cases.length - 1 ? findsNothing : findsOneWidget);
      expect(find.text('已保存的正文仍可阅读。'), findsOneWidget);
      expect(tester.takeException(), isNull);
    }
  });
}
