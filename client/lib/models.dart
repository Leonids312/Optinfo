typedef Json = Map<String, dynamic>;

Json object(dynamic value) =>
    value is Map ? Map<String, dynamic>.from(value) : {};
List<Json> objects(dynamic value) =>
    value is List ? value.map(object).toList() : [];
List<String> strings(dynamic value) =>
    value is List ? value.map((e) => '$e').toList() : [];
String string(dynamic value) => value == null ? '' : '$value';
String dateOnly(dynamic value) =>
    string(value).split('T').first.split(' ').first;
String shanghaiToday() => DateTime.now()
    .toUtc()
    .add(const Duration(hours: 8))
    .toIso8601String()
    .substring(0, 10);

class NewsItem {
  final Json raw;
  NewsItem(this.raw);
  String get id => string(raw['id']);
  String get title => string(raw['title']);
  String get summary => string(raw['summary']);
  String get kind => string(raw['kind']);
  String get section => string(raw['section']);
  String get journal => string(raw['journal']);
  String get date => dateOnly(raw['published_at']).isNotEmpty
      ? dateOnly(raw['published_at'])
      : dateOnly(raw['briefing_date']);
  String get sourceName => string(raw['source_name']);
  String get sourceUrl => string(raw['source_url']);
  String get evidence => string(raw['evidence_level']);
  bool get bookmarked => raw['bookmarked'] == true;
  List<String> get companyIds => strings(raw['company_ids']);
  List<String> get tags => strings(raw['tags']);
  Json get content => object(raw['content']);
  List<Json> get figures => objects(raw['figures']);
  List<Json> get sources => objects(raw['sources']);
}

class Company {
  final Json raw;
  Company(this.raw);
  String get id => string(raw['id']);
  String get name => string(raw['name']);
  String get nameEn => string(raw['name_en']);
  String get logo => string(raw['logo_url']);
  String get description => string(raw['description']);
  String get website => string(raw['website']);
  bool get bookmarked => raw['bookmarked'] == true;
  List<String> get businesses => strings(raw['businesses']);
  Json get compensation => object(raw['compensation']);
  String get listing => raw['listed'] == true
      ? (string(raw['listing']).isEmpty ? '已上市' : string(raw['listing']))
      : raw['listed'] == false
          ? '未上市'
          : '上市信息待核实';
}
