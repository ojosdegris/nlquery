$(document).ready(function(){
    var loading = false;
    $('#query-form').on('submit', function(e){
        e.preventDefault();
        if(loading){
            return;
        }
        var query = $('#query').val();
        loading = true;
        $('#search').hide();
        $('#loader').show();
        $.post(
            '/query',
            JSON.stringify({'q': query}),
            function(resp){
                $('#ans-resp').text(resp.data.plain);
                $('#query-resp').text(resp.data.query);
                $('#params-resp').text(JSON.stringify(resp.data.params));
                $('#tree-resp').html(resp.data.tree);
                $('#sparql-resp').html(''+resp.data.sparql_query);

                loading = false;
                $('#search').show();
                $('#loader').hide();
            },
            'json'
        ).error(function(){
            loading = false;
            $('#search').show();
            $('#loader').hide();
        });
    });
});